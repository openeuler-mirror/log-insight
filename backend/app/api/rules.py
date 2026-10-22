"""规则管理: 管理员增删改查 + 正则安全校验 + 脚本路径校验 + 变更审计 + 版本热加载."""
import ast
import re
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import RuleCreate, RuleInfo, RuleUpdate
from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import ensure_team_perm, get_current_user
from app.models.rule import MatchType, Rule, RuleAuditLog
from app.models.user import User

router = APIRouter(prefix="/rules", tags=["rules"])

# 防灾难性回溯: 禁止"括号内含量词"的嵌套量子模式与 {m,,n} 畸形量词
# 注意: 不能笼统禁止 (?...) —— (?:...) 非捕获组等是常用且安全的写法
# (内置示例规则就用了 (?:A|B) 形式, 之前被误判)
DANGEROUS_REGEX = re.compile(r"\((?:[^()]*\+|\+[^()]*)\)|(\{.*,\s*,.*\})")


def _validate_script_path(path: str) -> None:
    """脚本路径校验: 相对路径 + .py 后缀 + 解析后必须在脚本根目录内 (防穿越)."""
    if not path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "脚本规则必须有 script_path")
    p = PurePosixPath(path.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or p.suffix != ".py":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "script_path 须为 .py 相对路径, 如 oom_burst.py")
    root = Path(get_settings().RULE_SCRIPT_DIR).resolve()
    if not (root / p).resolve().is_relative_to(root):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "script_path 不能逃逸脚本根目录")
    if not (root / p).is_file():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"脚本文件不存在: {path} (需位于 {root})")


def validate_rule(body: RuleCreate) -> None:
    if body.match_type == MatchType.regex:
        if not body.pattern:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "正则规则必须有 pattern")
        if DANGEROUS_REGEX.search(body.pattern):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "正则存在灾难性回溯风险, 请改写")
        try:
            re.compile(body.pattern)
        except re.error as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"正则语法错误: {e}")
    elif body.match_type == MatchType.keyword and not body.pattern:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "关键字规则必须有 pattern")
    elif body.match_type == MatchType.threshold:
        if not {"metric", "op", "value"} <= set(body.threshold.keys()):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "阈值规则需包含 metric/op/value")
    elif body.match_type == MatchType.script:
        _validate_script_path(body.script_path)


def _normalize_rule(body: RuleCreate) -> RuleCreate:
    """按类型清理无关字段: script 清空 pattern/threshold, 其余类型还原脚本字段默认值."""
    if body.match_type == MatchType.script:
        body.pattern = ""
        body.threshold = {}
    else:
        body.script_path = ""
        body.script_params = {}
        body.script_timeout = 5
    return body


def _rule_snapshot(rule: Rule) -> dict:
    """规则快照 (审计 detail 用): 经 RuleInfo 转 JSON 安全类型, 避免 datetime 无法写入 JSONB."""
    return RuleInfo.model_validate(rule).model_dump(mode="json")


# 内容字段: 参与「内容版本」比较; enabled 只是状态开关, 不计入
_CONTENT_FIELDS = ("name", "description", "match_type", "pattern", "threshold",
                   "script_path", "script_params", "script_timeout",
                   "severity", "log_type", "server_type", "priority")


def _content_changed(rule: Rule, body: RuleCreate) -> bool:
    """内容字段是否有实际变更 (仅启停不算)."""
    new = body.model_dump()
    return any(getattr(rule, f) != new.get(f) for f in _CONTENT_FIELDS)


@router.get("/scripts")
async def list_rule_scripts(
    user: Annotated[User, Depends(get_current_user)],
):
    """扫描 RULE_SCRIPT_DIR 下可用脚本 (供前端下拉选择), 返回相对路径 + 首行 docstring."""
    root = Path(get_settings().RULE_SCRIPT_DIR).resolve()
    if not root.is_dir():
        return []
    out = []
    for f in sorted(root.glob("*.py")):
        if f.name.startswith("_"):
            continue  # _shared/ 等内部模块不对外暴露
        doc = ""
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
            raw_doc = ast.get_docstring(tree)
            if raw_doc:
                doc = raw_doc.strip().splitlines()[0]
        except (OSError, SyntaxError, ValueError):
            pass
        out.append({"path": f.name, "doc": doc})
    return out


@router.get("/scripts/content")
async def read_rule_script(
    path: str,
    user: Annotated[User, Depends(get_current_user)],
):
    """读取脚本规则源码 (供前端查看脚本规则详情); 校验同脚本路径, 防止目录穿越."""
    p = PurePosixPath(path.replace("\\", "/"))
    if not path or p.is_absolute() or ".." in p.parts or p.suffix != ".py":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法脚本路径")
    root = Path(get_settings().RULE_SCRIPT_DIR).resolve()
    target = (root / p).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "脚本文件不存在")
    try:
        content = target.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"脚本读取失败: {e}")
    return {"path": p.name, "content": content}


@router.get("", response_model=list[RuleInfo])
async def list_rules(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    team_id: int | None = None,
    enabled_only: bool = False,
):
    """列出规则: 不传 team_id 只返回平台通用规则; 传 team_id 返回「通用 ∪ 本团队」."""
    query = select(Rule).order_by(Rule.priority, Rule.id)
    if team_id is None:
        query = query.where(Rule.team_id.is_(None))
    else:
        await ensure_team_perm(db, user, team_id, "rule:view")
        query = query.where(or_(Rule.team_id.is_(None), Rule.team_id == team_id))
    if enabled_only:
        query = query.where(Rule.enabled.is_(True))
    return (await db.execute(query)).scalars().all()


@router.post("", response_model=RuleInfo, status_code=201)
async def create_rule(
    body: RuleCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    if "rule:manage" not in {p.code for r in user.roles for p in r.permissions}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要规则管理权限")
    validate_rule(body)
    _normalize_rule(body)
    rule = Rule(**body.model_dump(), version=1, created_by=user.id, team_id=None)
    db.add(rule)
    await db.flush()  # 先落库生成 rule.id, 审计日志外键依赖它
    db.add(RuleAuditLog(rule_id=rule.id, rule_name=rule.name, action="create",
                        operator_id=user.id, detail=body.model_dump()))
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put("/{rule_id}", response_model=RuleInfo)
async def update_rule(
    rule_id: int,
    body: RuleUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    if "rule:manage" not in {p.code for r in user.roles for p in r.permissions}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要规则管理权限")
    validate_rule(body)
    _normalize_rule(body)
    rule = (await db.execute(select(Rule).where(Rule.id == rule_id))).scalar_one_or_none()
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "规则不存在")
    if rule.team_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "团队专属规则请通过团队规则接口管理")
    # 内容版本: 只有内容字段真的变了才 +1; 单纯启停不改版本
    old = _rule_snapshot(rule)
    changed = _content_changed(rule, body)
    for key, value in body.model_dump().items():
        setattr(rule, key, value)
    if changed:
        rule.version += 1
    db.add(RuleAuditLog(rule_id=rule.id, rule_name=rule.name, action="update",
                        operator_id=user.id, detail={"old": old, "new": body.model_dump(mode="json")}))
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/{rule_id}")
async def delete_rule(
    rule_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """删除平台通用规则 (含内置规则, 需 rule:manage); 团队规则请用团队接口."""
    if "rule:manage" not in {p.code for r in user.roles for p in r.permissions}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要规则管理权限")
    rule = (await db.execute(select(Rule).where(Rule.id == rule_id))).scalar_one_or_none()
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "规则不存在")
    if rule.team_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "团队专属规则请通过团队规则接口管理")
    db.add(RuleAuditLog(rule_id=rule.id, rule_name=rule.name, action="delete",
                        operator_id=user.id, detail={}))
    await db.delete(rule)
    await db.commit()
    return {"detail": "已删除"}


# ---------- 团队级规则管理 (团队管理员定制本团队巡检规则) ----------

team_router = APIRouter(prefix="/teams/{team_id}/rules", tags=["rules"])


async def _ensure_team_rule_admin(db: AsyncSession, user: User, team_id: int) -> None:
    """团队规则写操作权限: 团队角色具备 rule:manage_team (平台管理员直通)."""
    await ensure_team_perm(db, user, team_id, "rule:manage_team")


def _reject_team_script(body: RuleCreate) -> None:
    """团队规则暂不开放脚本类型 (脚本等价于在服务器执行代码, 安全面收敛到平台维护)."""
    if body.match_type == MatchType.script:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "团队规则暂不支持脚本类型, 请使用关键字/正则/阈值")


@team_router.get("", response_model=list[RuleInfo])
async def list_team_rules(
    team_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """团队规则视图: 平台通用规则(只读) + 本团队专属规则."""
    await ensure_team_perm(db, user, team_id, "rule:view")
    query = (select(Rule)
             .where(or_(Rule.team_id.is_(None), Rule.team_id == team_id))
             .order_by(Rule.priority, Rule.id))
    return (await db.execute(query)).scalars().all()


@team_router.post("", response_model=RuleInfo, status_code=201)
async def create_team_rule(
    team_id: int,
    body: RuleCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """新增团队专属规则 (团队管理员)."""
    await _ensure_team_rule_admin(db, user, team_id)
    _reject_team_script(body)
    validate_rule(body)
    _normalize_rule(body)
    rule = Rule(**body.model_dump(), version=1, created_by=user.id, team_id=team_id)
    db.add(rule)
    await db.flush()
    db.add(RuleAuditLog(rule_id=rule.id, rule_name=rule.name, action="create",
                        operator_id=user.id, detail=body.model_dump()))
    await db.commit()
    await db.refresh(rule)
    return rule


@team_router.put("/{rule_id}", response_model=RuleInfo)
async def update_team_rule(
    team_id: int,
    rule_id: int,
    body: RuleUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """编辑团队专属规则 (团队管理员); 仅限本团队规则."""
    await _ensure_team_rule_admin(db, user, team_id)
    _reject_team_script(body)
    validate_rule(body)
    _normalize_rule(body)
    rule = (await db.execute(select(Rule).where(Rule.id == rule_id))).scalar_one_or_none()
    if rule is None or rule.team_id != team_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "团队规则不存在")
    # 内容版本: 只有内容字段真的变了才 +1; 单纯启停不改版本
    old = _rule_snapshot(rule)
    changed = _content_changed(rule, body)
    for key, value in body.model_dump().items():
        setattr(rule, key, value)
    if changed:
        rule.version += 1
    db.add(RuleAuditLog(rule_id=rule.id, rule_name=rule.name, action="update",
                        operator_id=user.id, detail={"old": old, "new": body.model_dump(mode="json")}))
    await db.commit()
    await db.refresh(rule)
    return rule


@team_router.delete("/{rule_id}")
async def delete_team_rule(
    team_id: int,
    rule_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """删除团队专属规则 (团队管理员); 仅限本团队规则."""
    await _ensure_team_rule_admin(db, user, team_id)
    rule = (await db.execute(select(Rule).where(Rule.id == rule_id))).scalar_one_or_none()
    if rule is None or rule.team_id != team_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "团队规则不存在")
    db.add(RuleAuditLog(rule_id=rule.id, rule_name=rule.name, action="delete",
                        operator_id=user.id, detail={}))
    await db.delete(rule)
    await db.commit()
    return {"detail": "已删除"}
