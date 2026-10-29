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
from pydantic import BaseModel, Field, model_validator
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import delete as sa_delete, func, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.database import AsyncSessionLocal, get_db
from app.core.deps import get_current_user, is_platform_admin
from app.models.diagnosis import (DiagnosisMessage, DiagnosisSession,
                                  DiagnosisUserConfig, DiagnosisUserProvider,
                                  DiagnosisUserSelection)
from app.models.llm import (DiagnosisUserTeamSelection, LlmUsageEvent,
                            TeamLlmModel, TeamLlmProvider)
from app.models.team import Team
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
    title: str | None = Field(default=None, max_length=80)
    selected_kb_ids: list[int] | None = None
    kb_top_k: int | None = Field(default=None, ge=1, le=50)


class FileRef(BaseModel):
    """附件引用: 任务日志传 task_id, 本地磁盘文件传 path, 二者至少一个."""
    task_id: uuid.UUID | None = None
    path: str = ""
    name: str = ""

    @model_validator(mode="after")
    def _require_source(self) -> FileRef:
        if self.task_id is None and not self.path:
            raise ValueError("task_id 与 path 至少提供一个")
        return self


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
            "selected_kb_ids": list(s.selected_kb_ids or []),
            "kb_top_k": int(s.kb_top_k or 10),
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


def _session_access(user: User, sess: DiagnosisSession) -> tuple[bool, bool]:
    """会话数据域: 返回 (可读, 可管理).

    历史会话只属于创建者, 其他用户不可见; 平台管理员保留排障访问.
    """
    if is_platform_admin(user) or sess.user_id == user.id:
        return True, True
    return False, False


async def _load_session(db: AsyncSession, user: User, session_id: uuid.UUID, *,
                        manage: bool = False) -> DiagnosisSession:
    s = (await db.execute(select(DiagnosisSession).where(
        DiagnosisSession.id == session_id))).scalar_one_or_none()
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "诊断会话不存在")
    readable, manageable = _session_access(user, s)
    if not readable:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权查看该会话")
    if manage and not manageable:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只有创建者可以管理该会话")
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


def _local_attachment(ref: FileRef) -> Attachment:
    """本地磁盘附件 (本地目录数据源): 用户自选路径, 仅校验存在性."""
    path = Path(ref.path)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "本地文件不存在")
    return Attachment(task_id="local", task_name="本地文件", name=path.name,
                      abs_path=path, size=path.stat().st_size)


async def _attachment(db: AsyncSession, user: User, ref: FileRef) -> Attachment:
    if ref.task_id is None:
        return _local_attachment(ref)
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


# ---------- 会话 ----------
@router.get("/sessions")
async def list_sessions(db: Annotated[AsyncSession, Depends(get_db)],
                        user: Annotated[User, Depends(get_current_user)],
                        team_id: int | None = None,
                        limit: int = Query(100, ge=1, le=200)):
    """历史会话: 仅当前用户自己的会话, 按更新时间倒序."""
    query = select(DiagnosisSession).where(DiagnosisSession.user_id == user.id)
    if team_id is not None:
        query = query.where(DiagnosisSession.team_id == team_id)
    rows = list((await db.execute(
        query.order_by(DiagnosisSession.updated_at.desc()).limit(limit))).scalars().all())
    if not rows:
        return []

    ids = [s.id for s in rows]
    team_ids = {s.team_id for s in rows if s.team_id}
    teams = {t.id: t.name for t in (await db.execute(
        select(Team).where(Team.id.in_(team_ids)))).scalars().all()} if team_ids else {}
    counts = {sid: n for sid, n in (await db.execute(
        select(DiagnosisMessage.session_id, func.count(DiagnosisMessage.id))
        .where(DiagnosisMessage.session_id.in_(ids))
        .group_by(DiagnosisMessage.session_id))).all()}

    return [_session_info(
        s, team_name=teams.get(s.team_id or 0, ""), can_manage=True,
        message_count=counts.get(s.id, 0)) for s in rows]


@router.post("/sessions")
async def create_session(body: SessionCreate,
                         db: Annotated[AsyncSession, Depends(get_db)],
                         user: Annotated[User, Depends(get_current_user)]):
    status_info = await _team_status(db, user, body.team_id)
    team_id = int(status_info.get("teamId") or 0) or None
    s = DiagnosisSession(
        user_id=user.id, title=(body.title.strip() or "新的诊断"),
        team_id=team_id, provider=status_info.get("provider") or "",
        model=status_info.get("model") or "",
        model_name=status_info.get("modelName") or "",
        reasoning=status_info.get("reasoning") or "",
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return _session_info(s)


@router.patch("/sessions/{session_id}")
async def patch_session(session_id: uuid.UUID, body: SessionPatch,
                        db: Annotated[AsyncSession, Depends(get_db)],
                        user: Annotated[User, Depends(get_current_user)]):
    """更新会话标题 / 知识库选择 / TopK."""
    s = await _load_session(db, user, session_id, manage=True)
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "标题不能为空")
        s.title = title
    if body.kb_top_k is not None:
        s.kb_top_k = body.kb_top_k
    if body.selected_kb_ids is not None:
        ids = list(dict.fromkeys(int(x) for x in body.selected_kb_ids))
        if ids and s.team_id:
            from app.models.kb import KnowledgeBase
            valid = (await db.execute(select(KnowledgeBase.id).where(
                KnowledgeBase.team_id == s.team_id,
                KnowledgeBase.id.in_(ids),
                KnowledgeBase.deleted_at.is_(None),
                KnowledgeBase.status == "active",
            ))).scalars().all()
            ids = [i for i in ids if i in set(valid)]
        elif ids and not s.team_id:
            ids = []
        s.selected_kb_ids = ids
    if (body.title is None and body.selected_kb_ids is None and body.kb_top_k is None):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "无更新字段")
    s.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(s)
    return _session_info(s)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: uuid.UUID,
                         db: Annotated[AsyncSession, Depends(get_db)],
                         user: Annotated[User, Depends(get_current_user)]):
    s = await _load_session(db, user, session_id, manage=True)
    await db.execute(sa_delete(DiagnosisMessage).where(DiagnosisMessage.session_id == s.id))
    await db.delete(s)
    await db.commit()
    return {"detail": "会话已删除"}


@router.get("/sessions/{session_id}/messages")
async def list_messages(session_id: uuid.UUID,
                        db: Annotated[AsyncSession, Depends(get_db)],
                        user: Annotated[User, Depends(get_current_user)]):
    await _load_session(db, user, session_id)
    rows = (await db.execute(
        select(DiagnosisMessage).where(DiagnosisMessage.session_id == session_id)
        .order_by(DiagnosisMessage.id.asc()).limit(500))).scalars().all()
    return [_message_info(m) for m in rows]


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: uuid.UUID, body: SendMessage,
                       db: Annotated[AsyncSession, Depends(get_db)],
                       user: Annotated[User, Depends(get_current_user)]):
    """发送一轮问题, 以 SSE 流式返回智能体事件."""
    sess = await _load_session(db, user, session_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "问题不能为空")

    attachments = [await _attachment(db, user, ref) for ref in body.files]

    team_id = sess.team_id
    model_row_id = body.model_row_id
    if model_row_id:
        row = await db.get(TeamLlmModel, model_row_id)
        if row is not None:
            team_id = row.team_id
    if model_row_id is None and team_id and sess.model:
        # 继续历史会话: 未显式换模型时沿用该会话上次的模型
        row = (await db.execute(select(TeamLlmModel).where(
            TeamLlmModel.team_id == team_id,
            TeamLlmModel.model_id == sess.model,
            TeamLlmModel.enabled.is_(True),
        ).limit(1))).scalar_one_or_none()
        if row is not None:
            model_row_id = row.id
    if team_id is None:
        status_info = await _team_status(db, user)
        team_id = int(status_info.get("teamId") or 0) or None
    resolved = await resolve_team_model(db, user, team_id, model_row_id) \
        if team_id else None
    provider = resolved.provider if resolved and resolved.ok else ""
    model = resolved.model if resolved and resolved.ok else ""
    model_name = resolved.model_name if resolved and resolved.ok else ""
    key = resolved.api_key if resolved and resolved.ok else ""
    api_base = resolved.api_base if resolved and resolved.ok else ""
    protocol = resolved.protocol if resolved and resolved.ok else ""
    provided = (body.reasoning or "").strip().lower()
    reasoning = provided if provided in THINKING_LEVELS else ""
    sel = await _team_selection(db, user, team_id) if team_id else None
    if (not reasoning and resolved and resolved.ok
            and sess.model == resolved.model and sess.reasoning):
        reasoning = sess.reasoning   # 继续历史会话: 沿用当时的推理强度
    if not reasoning and sel is not None:
        reasoning = sel.reasoning or ""
    if body.model_row_id and team_id and resolved and resolved.ok:
        sel = await _team_selection(db, user, team_id)
        if sel is None:
            sel = DiagnosisUserTeamSelection(user_id=user.id, team_id=team_id)
        sel.model_row_id = resolved.model_row_id
        sel.reasoning = reasoning
        sel.updated_at = datetime.now(timezone.utc)
        db.add(sel)

    if sess.title in ("", "新的诊断"):
        sess.title = text[:24]
    if resolved and resolved.ok:
        if sess.team_id and sess.model and sess.model != resolved.model:
            sess.dsh_session_id = ""
        sess.team_id = resolved.team_id
        sess.provider = resolved.provider
        sess.model = resolved.model
        sess.model_name = resolved.model_name
        sess.reasoning = reasoning
    sess.updated_at = datetime.now(timezone.utc)

    user_msg = DiagnosisMessage(
        session_id=sess.id, role="user",
        blocks=[{"kind": "text", "text": text},
                *[{"kind": "file", "name": a.name, "taskId": a.task_id} for a in attachments]],
    )
    db.add(user_msg)
    session_uuid = sess.id
    initial_dsh_sid = sess.dsh_session_id
    await db.commit()
    await db.refresh(user_msg)

    ready, reason = runtime_ready()
    if not ready:
        block_reason = reason
    elif resolved is None or not resolved.ok:
        block_reason = resolved.error if resolved else "请先在团队「大模型」中启用模型"
    elif not key:
        block_reason = "团队模型凭据不可用, 请团队管理员重新连接供应商"
    else:
        block_reason = ""
    if block_reason:
        err_msg = DiagnosisMessage(session_id=sess.id, role="assistant", blocks=[],
                                   error=block_reason)
        db.add(err_msg)
        await db.commit()
        await db.refresh(err_msg)

        async def not_ready():
            yield {"event": "error", "data": json.dumps({"message": block_reason},
                                                        ensure_ascii=False)}
            yield {"event": "done", "data": json.dumps(
                {"messageId": err_msg.id, "error": block_reason}, ensure_ascii=False)}

        return EventSourceResponse(not_ready())

    # 快照知识库选择（generator 内不再依赖 ORM 会话对象）
    kb_ids = list(sess.selected_kb_ids or [])
    kb_top_k = int(sess.kb_top_k or 10)
    retrieve_team_id = int(team_id or sess.team_id or 0)

    async def generator():
        blocks: list[dict] = []
        text_block: dict | None = None
        thought_block: dict | None = None
        tools: dict[str, dict] = {}
        plan_block: dict | None = None
        final_usage: dict = {}
        metrics: dict = {}
        dsh_sid = initial_dsh_sid
        error = ""
        started = time.monotonic()

        yield {"event": "phase", "data": json.dumps({"phase": "preparing"},
                                                    ensure_ascii=False)}

        # R1: 发消息前若已选库则自动检索并写入 kb_context.md
        if kb_ids and retrieve_team_id:
            try:
                from app.modules.kb.retrieve import citations_as_dicts, retrieve_for_diagnosis
                from app.modules.diagnosis.team_models import default_model_row
                chat = None
                async with AsyncSessionLocal() as kb_db:
                    default = await default_model_row(kb_db, retrieve_team_id)
                    if default is not None:
                        mrow, prow = default
                        ckey = decrypt_secret(prow.api_key_enc) if prow.api_key_enc else ""
                        ckey = ckey or (_global_key() if prow.provider == "deepseek" else "")
                        if ckey:
                            chat = {
                                "api_base": prow.api_base or "",
                                "api_key": ckey,
                                "model": mrow.model_id,
                                "protocol": prow.protocol or "messages",
                            }
                    r = await retrieve_for_diagnosis(
                        kb_db,
                        team_id=retrieve_team_id,
                        session_id=str(session_uuid),
                        selected_kb_ids=kb_ids,
                        user_text=text,
                        top_k=kb_top_k,
                        chat=chat,
                    )
                if r.citations:
                    kb_block = {
                        "kind": "kb",
                        "citations": citations_as_dicts(r.citations),
                        "queries": r.queries,
                        "note": r.note,
                    }
                    blocks.append(kb_block)
                    yield {"event": "kb", "data": json.dumps(kb_block, ensure_ascii=False)}
                elif r.note:
                    yield {"event": "kb", "data": json.dumps(
                        {"kind": "kb", "citations": [], "queries": r.queries, "note": r.note},
                        ensure_ascii=False)}
            except Exception as exc:  # noqa: BLE001
                logger.exception("诊断知识库检索失败")
                yield {"event": "kb", "data": json.dumps(
                    {"kind": "kb", "citations": [], "queries": [],
                     "note": f"知识库检索失败：{exc}"[:200]},
                    ensure_ascii=False)}

        async for ev in stream_prompt(session_id=str(session_uuid), dsh_session_id=dsh_sid,
                                      text=text, attachments=attachments,
                                      api_key=key, model=model, provider=provider,
                                      model_name=model_name,
                                      api_base=api_base, protocol=protocol,
                                      reasoning=reasoning):
            etype = ev.get("type")
            if etype in ("text", "thought"):
                target = text_block if etype == "text" else thought_block
                if target is None:
                    target = {"kind": etype, "text": ""}
                    blocks.append(target)
                    if etype == "text":
                        text_block = target
                    else:
                        thought_block = target
                target["text"] += ev.get("text", "")
                yield {"event": etype, "data": json.dumps(
                    {"text": ev.get("text", "")}, ensure_ascii=False)}
            elif etype == "tool":
                tool_id = ev.get("id") or f"t{len(tools) + 1}"
                block = tools.get(tool_id)
                if block is None:
                    block = {"kind": "tool", "id": tool_id, "name": "", "title": "",
                             "toolKind": "", "status": "running", "input": "", "output": ""}
                    tools[tool_id] = block
                    blocks.append(block)
                for field in ("title", "toolKind", "status", "input", "output"):
                    if ev.get(field):
                        block[field] = ev[field]
                block["name"] = ev.get("title") or block.get("name") or ev.get("toolKind") or "工具"
                if block["status"] in ("completed", "failed"):
                    block["status"] = "done" if block["status"] == "completed" else "failed"
                yield {"event": "tool", "data": json.dumps(block, ensure_ascii=False)}
            elif etype == "plan":
                if plan_block is None:
                    plan_block = {"kind": "plan", "entries": []}
                    blocks.append(plan_block)
                plan_block["entries"] = ev.get("entries", [])
                yield {"event": "plan", "data": json.dumps(plan_block, ensure_ascii=False)}
            elif etype == "commands":
                yield {"event": "commands", "data": json.dumps(
                    {"names": ev.get("names", [])}, ensure_ascii=False)}
            elif etype == "phase":
                yield {"event": "phase", "data": json.dumps({"phase": ev.get("phase")},
                                                            ensure_ascii=False)}
            elif etype == "session":
                dsh_sid = ev.get("dshSessionId") or dsh_sid
                yield {"event": "session", "data": json.dumps(
                    {"dshSessionId": dsh_sid}, ensure_ascii=False)}
            elif etype == "usage":
                final_usage.update(ev.get("usage") or {})
                yield {"event": "usage", "data": json.dumps(
                    {"usage": ev.get("usage") or {}}, ensure_ascii=False)}
            elif etype == "done":
                dsh_sid = ev.get("dshSessionId") or dsh_sid
                final_usage.update(ev.get("usage") or {})
                metrics = ev.get("metrics") or {}
                break
            elif etype == "error":
                error = ev.get("message") or "智能体运行失败"
                yield {"event": "error", "data": json.dumps(
                    {"message": error}, ensure_ascii=False)}
                break

        am = DiagnosisMessage(session_id=session_uuid, role="assistant", blocks=blocks,
                              usage=final_usage, error=error)
        async with AsyncSessionLocal() as stream_db:
            stream_db.add(am)
            row = await stream_db.get(DiagnosisSession, session_uuid)
            if row is not None:
                row.dsh_session_id = dsh_sid
                row.model = model
                row.provider = provider
                row.model_name = model_name
                row.reasoning = reasoning
                row.updated_at = datetime.now(timezone.utc)
            await stream_db.commit()
            await stream_db.refresh(am)
            if resolved is not None and resolved.ok:
                stream_db.add(LlmUsageEvent(
                    session_id=str(session_uuid), message_id=am.id,
                    team_id=resolved.team_id, user_id=user.id,
                    provider=provider, model=model, model_name=model_name,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    status="error" if error else "ok",
                    raw={"usage": final_usage, "metrics": metrics},
                    **normalize_usage(final_usage),
                ))
                await stream_db.commit()
        if dsh_sid:
            yield {"event": "session", "data": json.dumps(
                {"dshSessionId": dsh_sid}, ensure_ascii=False)}
        yield {"event": "usage", "data": json.dumps({"usage": final_usage},
                                                    ensure_ascii=False)}
        yield {"event": "done", "data": json.dumps(
            {"messageId": am.id, "usage": final_usage, "error": error},
            ensure_ascii=False)}

    return EventSourceResponse(generator())


# ---------- 文件 ----------
@router.get("/files")
async def file_tree(db: Annotated[AsyncSession, Depends(get_db)],
                    user: Annotated[User, Depends(get_current_user)]):
    tasks = await _visible_tasks(db, user)
    nodes = []
    for task in tasks:
        files = _files_of(task)
        if not files:
            continue
        children = [{
            "id": f"{task.id}|{rel}",
            "name": path.name,
            "path": str(path),
            "size": path.stat().st_size,
            "kind": "file",
        } for rel, path in files]
        created = task.created_at
        if created is not None and created.tzinfo is not None:
            created = created.astimezone(ZoneInfo("Asia/Shanghai"))
        nodes.append({
            "id": f"task-{task.id}",
            "name": task.name or task.filename or "未命名任务",
            "meta": f"{len(children)} 个文件 · {created:%m-%d %H:%M}" if created else "",
            "kind": "dir",
            "children": children,
        })
    return nodes


@router.get("/files/content")
async def file_content(file_id: str = Query(...),
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    try:
        task_id, rel = file_id.split("|", 1)
        task_uuid = uuid.UUID(task_id)
    except (ValueError, AttributeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法文件标识")
    task = (await db.execute(select(Task).where(Task.id == task_uuid))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    if task.user_id != user.id and not is_platform_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权读取该文件")
    path = _resolve_file(task, rel)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在")

    ext = path.suffix.lower()
    size = path.stat().st_size
    if ext in ARCHIVE_EXT:
        return {"kind": "notice", "lang": "text", "size": size,
                "content": f"该文件是压缩包（{path.name}），无法直接预览。\n"
                           f"可先解压后上传具体日志，或直接把整个日志包上传到平台。"}
    if ext in IMAGE_EXT and size <= 4 * 1024 * 1024:
        data = base64.b64encode(path.read_bytes()).decode()
        mime = "image/svg+xml" if ext == ".svg" else f"image/{ext.lstrip('.')}"
        return {"kind": "image", "content": f"data:{mime};base64,{data}", "size": size}

    limit = settings.AGENT_STREAM_MAX_BYTES
    with open(path, "rb") as f:
        raw = f.read(limit)
    if b"\x00" in raw[:4096]:
        return {"kind": "notice", "lang": "text", "size": size,
                "content": f"该文件是二进制文件（{path.name}），无法直接预览。"}
    content = raw.decode("utf-8", errors="replace")
    lang = "json" if ext in (".json", ".jsonl") else "log" if ext in (".log", ".out", ".err") else "text"
    header = ""
    if size > limit:
        header = f"# 文件较大，仅显示前 {limit // 1024 // 1024} MB（共 {size // 1024 // 1024} MB）\n"
    return {"kind": "text", "content": header + content, "lang": lang, "size": size,
            "truncated": size > limit}
