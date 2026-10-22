"""智能诊断 API: 会话管理 / 文件浏览 / DSH 智能体流式对话."""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import and_, delete as sa_delete, func, or_, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.database import AsyncSessionLocal, get_db
from app.core.deps import get_current_user, is_platform_admin, load_team_role
from app.core.team_rbac import team_role_has_perm
from app.models.diagnosis import (DiagnosisMessage, DiagnosisSession,
                                  DiagnosisUserConfig, DiagnosisUserProvider,
                                  DiagnosisUserSelection)
from app.models.llm import (DiagnosisUserTeamSelection, LlmUsageEvent,
                            TeamLlmModel, TeamLlmProvider)
from app.models.team import Team, TeamMember
from app.models.task import Task
from app.models.user import User
from app.modules.agent.discovery import discover_models
from app.modules.agent.probe import reasoning_options
from app.modules.agent.providers import (PROVIDERS, default_model, display_name,
                                         normalize_model, spec_of)
from app.modules.agent.service import (Attachment, list_skills, normalize_usage,
                                       runtime_ready, stream_prompt)
from app.modules.diagnosis.team_models import (default_model_row, resolve_team_model,
                                               team_models, user_teams)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/diagnosis", tags=["diagnosis"])
settings = get_settings()

TEXT_EXT = {".log", ".txt", ".json", ".jsonl", ".csv", ".conf", ".ini", ".yaml", ".yml",
            ".xml", ".out", ".err", ".sar", ".dmesg", ".md"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
ARCHIVE_EXT = {".zip", ".gz", ".tgz", ".tar", ".bz2", ".xz", ".7z"}


class SessionCreate(BaseModel):
    title: str = ""
    team_id: int | None = None


class SessionPatch(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class FileRef(BaseModel):
    task_id: uuid.UUID
    name: str = ""


class SendMessage(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    files: list[FileRef] = Field(default_factory=list)
    model_row_id: int | None = None
    reasoning: str = ""


class TeamSelectionIn(BaseModel):
    team_id: int
    model_row_id: int
    reasoning: str = ""


class AgentSelectionIn(BaseModel):
    provider: str = "deepseek"
    model: str = ""
    model_name: str = ""
    reasoning: str = ""


class ProviderConnectionIn(BaseModel):
    api_key: str | None = None
    api_base: str = ""
    protocol: str = "messages"


class ModelQuery(BaseModel):
    provider: str = "deepseek"
    model: str = ""
    api_key: str | None = None
    api_base: str = ""
    protocol: str = ""


class ReasoningQuery(BaseModel):
    provider: str = "deepseek"
    model: str = ""


THINKING_LEVELS = {"off", "minimal", "low", "medium", "high", "xhigh", "max"}


def _iso(dt: datetime | None) -> str:
    return dt.isoformat() if dt else ""


def _session_info(s: DiagnosisSession, *, team_name: str = "", owner_name: str = "",
                  can_manage: bool = True, message_count: int = 0) -> dict:
    return {"id": str(s.id), "title": s.title, "model": s.model,
            "team_id": s.team_id, "provider": s.provider,
            "model_name": s.model_name, "reasoning": s.reasoning,
            "user_id": s.user_id, "owner_name": owner_name,
            "team_name": team_name, "can_manage": can_manage,
            "message_count": message_count,
            "created_at": _iso(s.created_at), "updated_at": _iso(s.updated_at)}


def _message_info(m: DiagnosisMessage) -> dict:
    return {"id": m.id, "role": m.role, "blocks": m.blocks or [],
            "usage": m.usage or {}, "error": m.error,
            "created_at": _iso(m.created_at)}


async def _user_config(db: AsyncSession, user: User) -> DiagnosisUserConfig | None:
    return await db.get(DiagnosisUserConfig, user.id)


def _global_key() -> str:
    return settings.AGENT_API_KEY or os.environ.get("DEEPSEEK_API_KEY", "")


async def _provider_credential(db: AsyncSession, user: User,
                               provider: str) -> DiagnosisUserProvider | None:
    return await db.get(DiagnosisUserProvider, (user.id, spec_of(provider).id))


async def _user_selections(db: AsyncSession, user: User) -> dict[str, DiagnosisUserSelection]:
    rows = (await db.execute(select(DiagnosisUserSelection).where(
        DiagnosisUserSelection.user_id == user.id))).scalars().all()
    return {row.provider: row for row in rows}


def _credential_key(cred: DiagnosisUserProvider | None) -> str:
    return decrypt_secret(cred.api_key_enc) if cred and cred.api_key_enc else ""


def _platform_key(provider: str) -> str:
    return _global_key() if spec_of(provider).id == "deepseek" else ""


def _resolve_key(cred: DiagnosisUserProvider | None, cfg: DiagnosisUserConfig | None,
                 provider: str) -> str:
    """连接凭据优先; 其次旧版单供应商密钥; DeepSeek 可用平台默认密钥."""
    target = spec_of(provider).id
    user_key = _credential_key(cred)
    if user_key:
        return user_key
    if cfg and spec_of(cfg.provider or "deepseek").id == target and cfg.api_key_enc:
        legacy = decrypt_secret(cfg.api_key_enc)
        if legacy:
            return legacy
    return _platform_key(target)


def _connection_state(cred: DiagnosisUserProvider | None, provider: str) -> dict:
    spec = spec_of(provider)
    user_key = _credential_key(cred)
    platform = not user_key and bool(_platform_key(spec.id))
    return {
        "connected": bool(user_key or platform),
        "source": "user" if user_key else ("platform" if platform else ""),
        "keyHint": cred.key_hint if user_key else ("平台默认" if platform else ""),
        "apiBase": (cred.api_base if cred else "") or spec.default_base,
        "protocol": (cred.protocol if cred else "") or spec.protocols[0],
    }


def _config_of(cfg: DiagnosisUserConfig | None) -> dict:
    """当前模型选择: 供应商 / 模型 / 显示名称 / 推理强度."""
    provider = spec_of(cfg.provider if cfg else "deepseek").id
    spec = spec_of(provider)
    raw_model = (cfg.model if cfg else "") or (
        settings.AGENT_MODEL if provider == "deepseek" else "")
    model = normalize_model(provider, raw_model or default_model(provider)[0])
    name = display_name(provider, model, cfg.model_name if cfg else "")
    reasoning = (cfg.reasoning if cfg else "") or ""
    return {"provider": provider, "model": model, "model_name": name,
            "reasoning": reasoning, "protocol": spec.protocols[0]}


def _config_status(cfg: DiagnosisUserConfig | None,
                   cred: DiagnosisUserProvider | None) -> dict:
    conf = _config_of(cfg)
    provider = conf["provider"]
    spec = spec_of(provider)
    key = _resolve_key(cred, cfg, provider)
    invalid_key = bool(cred and cred.api_key_enc and not _credential_key(cred))
    conn = _connection_state(cred, provider)
    runtime_ok, runtime_reason = runtime_ready()
    if not runtime_ok:
        ready, reason = False, runtime_reason
    elif invalid_key and not key:
        ready, reason = False, "已保存的密钥无法解密，请重新连接供应商"
    elif not conn["connected"]:
        ready, reason = False, f"请先在供应商连接中连接 {spec.label}"
    elif not conf["model"]:
        ready, reason = False, "请先选择模型"
    else:
        ready, reason = True, ""
    return {
        "enabled": settings.AGENT_ENABLED,
        "ready": ready,
        "reason": reason,
        "provider": provider,
        "model": conf["model"],
        "modelName": conf["model_name"],
        "reasoning": conf["reasoning"],
        "api_base": conn["apiBase"] or spec.default_base,
        "protocol": conn["protocol"],
        "has_key": bool(key),
        "user_key": bool(_credential_key(cred)),
        "provider_connected": conn["connected"],
        "key_source": conn["source"],
        "key_hint": conn["keyHint"],
        "permission": settings.AGENT_PERMISSION_MODE,
        "models": [],
        "skills": list_skills(),
    }


async def _team_selection(db: AsyncSession, user: User, team_id: int):
    return await db.get(DiagnosisUserTeamSelection, (user.id, team_id))


async def _team_status(db: AsyncSession, user: User, team_id: int | None = None) -> dict:
    """团队级模型状态: 当前团队、当前模型、是否就绪."""
    out = {
        "enabled": settings.AGENT_ENABLED,
        "ready": False,
        "reason": "",
        "teamId": 0,
        "teamName": "",
        "provider": "",
        "model": "",
        "modelName": "",
        "reasoning": "",
        "api_base": "",
        "protocol": "",
        "has_key": False,
        "provider_connected": False,
        "key_source": "",
        "key_hint": "",
        "permission": settings.AGENT_PERMISSION_MODE,
        "models": [],
        "skills": list_skills(),
    }
    runtime_ok, runtime_reason = runtime_ready()
    if not runtime_ok:
        out["reason"] = runtime_reason
        return out
    teams = await user_teams(db, user, all_for_admin=True)
    if not teams:
        out["reason"] = "请先加入一个团队, 并在团队「大模型」中启用模型"
        return out
    selections = {s.team_id: s for s in (await db.execute(
        select(DiagnosisUserTeamSelection).where(
            DiagnosisUserTeamSelection.user_id == user.id))).scalars().all()}
    team = next((t for t in teams if t.id == team_id), None) if team_id else None
    if team_id and team is None:
        out["reason"] = "无权访问该团队"
        return out
    if team is None:
        team = next((t for t in teams if t.id in selections and selections[t.id].model_row_id), None)
    if team is None:
        team = teams[0]
    out["teamId"] = team.id
    out["teamName"] = team.name
    sel = selections.get(team.id)
    row = None
    prow = None
    if sel and sel.model_row_id:
        row = await db.get(TeamLlmModel, sel.model_row_id)
        if row is not None and (row.team_id != team.id or not row.enabled):
            row = None
    if row is None:
        found = await default_model_row(db, team.id)
        if found is not None:
            row, prow = found
    else:
        prow = await db.get(TeamLlmProvider, row.provider_id)
    if row is None or prow is None:
        out["reason"] = "团队尚未配置可用模型, 请团队管理员在「大模型」中启用"
        return out
    spec = spec_of(prow.provider)
    key = decrypt_secret(prow.api_key_enc) if prow.api_key_enc else ""
    source = "team" if key else ""
    if not key and spec.id == "deepseek":
        key = _global_key()
        source = "platform" if key else ""
    out.update({
        "provider": spec.id,
        "model": row.model_id,
        "modelName": row.display_name or row.model_id,
        "reasoning": (sel.reasoning if sel and sel.model_row_id == row.id else "") or "",
        "api_base": prow.api_base or spec.default_base,
        "protocol": prow.protocol or spec.protocols[0],
        "has_key": bool(key),
        "provider_connected": bool(key),
        "key_source": source,
        "key_hint": prow.key_hint if source == "team" else ("平台默认" if source == "platform" else ""),
    })
    if not prow.enabled:
        out["reason"] = "该供应商已被团队停用"
    elif not key:
        out["reason"] = "团队尚未配置该供应商的 API Key"
    else:
        out["ready"] = True
    return out


def _valid_base(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if value and not value.startswith(("http://", "https://")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "接口地址必须以 http:// 或 https:// 开头")
    if len(value) > 256:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "接口地址过长")
    return value


async def _session_access(db: AsyncSession, user: User,
                          sess: DiagnosisSession) -> tuple[bool, bool]:
    """会话数据域: 返回 (可读, 可管理).

    - 创建者与平台管理员: 读写;
    - 团队成员 (llm:use): 可读可继续本团队会话;
    - 团队管理员 (llm:manage): 可重命名/删除;
    - 未归属团队的历史会话: 仅创建者可读.
    """
    if is_platform_admin(user) or sess.user_id == user.id:
        return True, True
    if sess.team_id:
        _team, role = await load_team_role(db, user, sess.team_id)
        if role is None:
            return False, False
        return (team_role_has_perm(role, "llm:use"),
                team_role_has_perm(role, "llm:manage"))
    return False, False


async def _load_session(db: AsyncSession, user: User, session_id: uuid.UUID, *,
                        manage: bool = False) -> DiagnosisSession:
    s = (await db.execute(select(DiagnosisSession).where(
        DiagnosisSession.id == session_id))).scalar_one_or_none()
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "诊断会话不存在")
    readable, manageable = await _session_access(db, user, s)
    if not readable:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权查看该会话")
    if manage and not manageable:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只有创建者或团队管理员可以管理该会话")
    return s


async def _visible_tasks(db: AsyncSession, user: User) -> list[Task]:
    query = select(Task).where(Task.storage_path != "").order_by(Task.created_at.desc()).limit(100)
    if not is_platform_admin(user):
        query = query.where(Task.user_id == user.id)
    return list((await db.execute(query)).scalars())


def _files_of(task: Task) -> list[tuple[str, Path]]:
    root = Path(task.storage_path)
    if not root.exists():
        return []
    if root.is_file():
        return [("", root)]
    out: list[tuple[str, Path]] = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out.append((str(p.relative_to(root)), p))
            if len(out) >= 200:
                break
    return out


def _resolve_file(task: Task, rel: str) -> Path:
    root = Path(task.storage_path)
    if root.is_file():
        return root.resolve()
    target = (root / rel).resolve()
    if root.resolve() not in target.parents:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法文件路径")
    return target


async def _attachment(db: AsyncSession, user: User, ref: FileRef) -> Attachment:
    task = (await db.execute(select(Task).where(Task.id == ref.task_id))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "日志任务不存在")
    if task.user_id != user.id and not is_platform_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权访问该任务日志")
    files = _files_of(task)
    if not files:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "日志文件已不存在")
    rel, path = files[0]
    if ref.name:
        for r, p in files:
            if r == ref.name or p.name == ref.name:
                rel, path = r, p
                break
    return Attachment(task_id=str(task.id), task_name=task.name or task.filename,
                      name=path.name, abs_path=path, size=path.stat().st_size)


# ---------- 运行时状态 / 用户模型配置 ----------
@router.get("/status")
async def agent_status(db: Annotated[AsyncSession, Depends(get_db)],
                       user: Annotated[User, Depends(get_current_user)],
                       team_id: int | None = None):
    return await _team_status(db, user, team_id)


@router.get("/catalog")
async def model_catalog(db: Annotated[AsyncSession, Depends(get_db)],
                        user: Annotated[User, Depends(get_current_user)]):
    """当前用户可用的团队模型, 按团队分组."""
    teams = await user_teams(db, user, all_for_admin=True)
    selections = {s.team_id: s for s in (await db.execute(
        select(DiagnosisUserTeamSelection).where(
            DiagnosisUserTeamSelection.user_id == user.id))).scalars().all()}
    out = []
    for team in teams:
        rows = await team_models(db, team.id)
        models = []
        for row, prow in rows:
            spec = spec_of(prow.provider)
            models.append({
                "row_id": row.id,
                "model_id": row.model_id,
                "name": row.display_name or row.model_id,
                "provider": spec.id,
                "provider_label": spec.label,
                "is_default": row.is_default,
            })
        sel = selections.get(team.id)
        out.append({
            "id": team.id,
            "name": team.name,
            "selection": ({"model_row_id": sel.model_row_id or 0,
                           "reasoning": sel.reasoning or ""} if sel else None),
            "models": models,
        })
    return {"teams": out}


@router.put("/selection")
async def update_selection(body: TeamSelectionIn,
                           db: Annotated[AsyncSession, Depends(get_db)],
                           user: Annotated[User, Depends(get_current_user)]):
    """保存用户在某团队下的模型选择 (切换团队时各自记忆)."""
    resolved = await resolve_team_model(db, user, body.team_id, body.model_row_id)
    if not resolved.ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, resolved.error)
    reasoning = (body.reasoning or "").strip().lower()
    sel = await _team_selection(db, user, body.team_id)
    if sel is None:
        sel = DiagnosisUserTeamSelection(user_id=user.id, team_id=body.team_id)
    changed = sel.model_row_id != resolved.model_row_id
    sel.model_row_id = resolved.model_row_id
    sel.reasoning = reasoning if reasoning in THINKING_LEVELS else ""
    sel.updated_at = datetime.now(timezone.utc)
    db.add(sel)
    if changed:
        # 换模型后重建 DSH 会话, 避免沿用旧路由
        await db.execute(sa_update(DiagnosisSession).where(
            DiagnosisSession.user_id == user.id,
            DiagnosisSession.team_id == body.team_id).values(dsh_session_id=""))
    await db.commit()
    return await _team_status(db, user, body.team_id)


@router.get("/providers", deprecated=True)
async def list_providers(db: Annotated[AsyncSession, Depends(get_db)],
                         user: Annotated[User, Depends(get_current_user)]):
    """可选供应商 + 当前用户的连接状态 (每个供应商独立保存凭据)."""
    rows = (await db.execute(select(DiagnosisUserProvider).where(
        DiagnosisUserProvider.user_id == user.id))).scalars().all()
    saved = {row.provider: row for row in rows}
    selections = await _user_selections(db, user)
    out = []
    for spec in PROVIDERS.values():
        sel = selections.get(spec.id)
        out.append({
            **spec.as_dict(),
            **_connection_state(saved.get(spec.id), spec.id),
            # 该供应商下最后一次的模型选择, 切回来时恢复
            "model": sel.model if sel else "",
            "modelName": sel.model_name if sel else "",
            "reasoning": sel.reasoning if sel else "",
        })
    return out


@router.put("/providers/{provider}", deprecated=True)
async def save_connection(provider: str, body: ProviderConnectionIn,
                          db: Annotated[AsyncSession, Depends(get_db)],
                          user: Annotated[User, Depends(get_current_user)]):
    """连接供应商: 保存该供应商自己的 API Key / 地址 / 协议."""
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "未知供应商")
    row = await _provider_credential(db, user, spec.id)
    raw_key = (body.api_key or "").strip()
    if len(raw_key) > 512:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "API Key 过长")
    api_base = _valid_base(body.api_base)
    if spec.requires_base and not api_base:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请填写接口地址")
    if not raw_key and not _credential_key(row):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请填写 API Key")
    if row is None:
        row = DiagnosisUserProvider(user_id=user.id, provider=spec.id)
    if raw_key:
        row.api_key_enc = encrypt_secret(raw_key)
        row.key_hint = "****" + raw_key[-4:]
    row.api_base = api_base
    row.protocol = body.protocol if body.protocol in spec.protocols else spec.protocols[0]
    row.updated_at = datetime.now(timezone.utc)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": spec.id, "provider": spec.id, "label": spec.label,
            **_connection_state(row, spec.id)}


@router.delete("/providers/{provider}", deprecated=True)
async def delete_connection(provider: str,
                            db: Annotated[AsyncSession, Depends(get_db)],
                            user: Annotated[User, Depends(get_current_user)]):
    """断开供应商: 删除该供应商的凭据, 其他供应商不受影响."""
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "未知供应商")
    row = await _provider_credential(db, user, spec.id)
    if row is not None:
        await db.delete(row)
        await db.commit()
    return {"id": spec.id, "provider": spec.id, "label": spec.label,
            **_connection_state(None, spec.id)}


@router.post("/config/models", deprecated=True)
async def list_models(body: ModelQuery,
                      db: Annotated[AsyncSession, Depends(get_db)],
                      user: Annotated[User, Depends(get_current_user)]):
    """读取供应商 /models 的真实模型 id 与显示名称 (不发起推理)."""
    provider = spec_of(body.provider).id
    spec = spec_of(provider)
    cfg = await _user_config(db, user)
    cred = await _provider_credential(db, user, provider)
    key = (body.api_key or "").strip() or _resolve_key(cred, cfg, provider)
    base = _valid_base(body.api_base) or (cred.api_base if cred else "")
    protocol = (body.protocol if body.protocol in spec.protocols else
                (cred.protocol if cred else spec.protocols[0]))
    return await discover_models(provider=provider, api_key=key, api_base=base,
                                 protocol=protocol)


@router.post("/config/reasoning", deprecated=True)
async def model_reasoning(body: ReasoningQuery):
    """模型支持的推理强度 (来自 DSH 目录能力, 不支持时列表为空)."""
    return await reasoning_options(spec_of(body.provider).id, body.model)


@router.put("/config", deprecated=True)
async def update_config(body: AgentSelectionIn,
                        db: Annotated[AsyncSession, Depends(get_db)],
                        user: Annotated[User, Depends(get_current_user)]):
    """保存模型选择; 连接凭据按供应商单独保存, 切换模型不需要重新连接."""
    cfg = await _user_config(db, user)
    provider = spec_of(body.provider).id
    spec = spec_of(provider)
    cred = await _provider_credential(db, user, provider)
    if not _resolve_key(cred, cfg, provider):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"请先连接 {spec.label}")
    model = normalize_model(provider, (body.model or "").strip() or default_model(provider)[0])
    if not model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请选择模型")
    if cfg is None:
        cfg = DiagnosisUserConfig(user_id=user.id)
    changed = (cfg.provider or "deepseek") != provider or (cfg.model or "") != model
    reasoning = (body.reasoning or "").strip().lower()
    cfg.provider = provider
    cfg.model = model
    cfg.model_name = display_name(provider, model, body.model_name)
    cfg.reasoning = reasoning if reasoning in THINKING_LEVELS else ""
    cfg.updated_at = datetime.now(timezone.utc)
    db.add(cfg)
    # 记住该供应商下的选择, 切到别的供应商再切回来时恢复
    sel = await db.get(DiagnosisUserSelection, (user.id, provider))
    if sel is None:
        sel = DiagnosisUserSelection(user_id=user.id, provider=provider)
    sel.model = cfg.model
    sel.model_name = cfg.model_name
    sel.reasoning = cfg.reasoning
    sel.updated_at = datetime.now(timezone.utc)
    db.add(sel)
    if changed:
        # 换模型/供应商后重新建立 DSH 会话, 避免沿用旧路由
        await db.execute(sa_update(DiagnosisSession)
                         .where(DiagnosisSession.user_id == user.id)
                         .values(dsh_session_id="", model=model))
    await db.commit()
    await db.refresh(cfg)
    return _config_status(cfg, cred)
