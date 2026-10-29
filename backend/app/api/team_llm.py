"""团队级大模型: 供应商连接、启用模型清单、推理能力与用量统计."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.database import get_db
from app.core.deps import get_current_user, require_team_perm
from app.models.llm import LlmUsageEvent, TeamLlmModel, TeamLlmProvider
from app.models.team import Team
from app.models.user import User
from app.modules.agent.discovery import discover_models
from app.modules.agent.probe import reasoning_options
from app.modules.agent.providers import PROVIDERS, spec_of

router = APIRouter(prefix="/teams/{team_id}/llm", tags=["team-llm"])
settings = get_settings()


class ProviderIn(BaseModel):
    api_key: str | None = None
    api_base: str = ""
    protocol: str = "messages"
    enabled: bool = True


class DiscoverIn(BaseModel):
    api_key: str | None = None
    api_base: str = ""
    protocol: str = ""


class ModelIn(BaseModel):
    model_id: str
    display_name: str = ""
    enabled: bool = True
    sort_order: int = 0


class ModelsIn(BaseModel):
    models: list[ModelIn]


class ModelPatch(BaseModel):
    display_name: str | None = None
    enabled: bool | None = None
    sort_order: int | None = None


class RerankIn(BaseModel):
    enabled: bool = False
    model: str = ""
    api_base: str = ""
    api_key: str | None = None  # 空=保持原密钥


def _rerank_public(cfg: dict | None) -> dict:
    cfg = cfg or {}
    has_key = bool(cfg.get("api_key_enc") or cfg.get("api_key"))
    return {
        "enabled": bool(cfg.get("enabled")),
        "model": str(cfg.get("model") or ""),
        "api_base": str(cfg.get("api_base") or ""),
        "has_key": has_key,
        "key_hint": cfg.get("key_hint") or ("****" if has_key else ""),
    }


@router.get("/rerank")
async def get_team_rerank(
    trio: Annotated[tuple, Depends(require_team_perm("llm:use"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _, team, _role = trio
    row = await db.get(Team, team.id)
    return _rerank_public(row.kb_rerank_config if row else {})


@router.put("/rerank")
async def put_team_rerank(
    body: RerankIn,
    trio: Annotated[tuple, Depends(require_team_perm("llm:manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _, team, _role = trio
    row = await db.get(Team, team.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "团队不存在")
    cfg = dict(row.kb_rerank_config or {})
    cfg["enabled"] = body.enabled
    cfg["model"] = (body.model or "").strip()
    cfg["api_base"] = _valid_base(body.api_base)
    raw_key = (body.api_key or "").strip()
    if raw_key:
        if len(raw_key) > 512:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "API Key 过长")
        cfg["api_key_enc"] = encrypt_secret(raw_key)
        cfg["key_hint"] = "****" + raw_key[-4:]
        cfg.pop("api_key", None)
    if body.enabled and not cfg.get("model"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "启用 Rerank 时必须填写模型 ID")
    if body.enabled and not (cfg.get("api_key_enc") or cfg.get("api_key")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "启用 Rerank 时必须配置 API Key")
    row.kb_rerank_config = cfg
    await db.commit()
    return _rerank_public(cfg)


def _valid_base(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if value and not value.startswith(("http://", "https://")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "接口地址必须以 http:// 或 https:// 开头")
    if len(value) > 256:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "接口地址过长")
    return value


def _platform_key(provider: str) -> str:
    if spec_of(provider).id != "deepseek":
        return ""
    return settings.AGENT_API_KEY or ""


async def _provider_row(db: AsyncSession, team_id: int, provider: str) -> TeamLlmProvider | None:
    return (await db.execute(select(TeamLlmProvider).where(
        TeamLlmProvider.team_id == team_id,
        TeamLlmProvider.provider == spec_of(provider).id,
    ))).scalar_one_or_none()


def _team_key(row: TeamLlmProvider | None) -> str:
    return decrypt_secret(row.api_key_enc) if row and row.api_key_enc else ""


def _connection_state(row: TeamLlmProvider | None, provider: str) -> dict:
    spec = spec_of(provider)
    key = _team_key(row)
    platform = not key and bool(_platform_key(spec.id))
    return {
        "connected": bool(key or platform),
        "source": "team" if key else ("platform" if platform else ""),
        "key_hint": row.key_hint if key else ("平台默认" if platform else ""),
        "api_base": (row.api_base if row else "") or spec.default_base,
        "protocol": (row.protocol if row else "") or spec.protocols[0],
        "enabled": row.enabled if row else True,
    }


async def _model_rows(db: AsyncSession, team_id: int) -> list[TeamLlmModel]:
    return list((await db.execute(
        select(TeamLlmModel).where(TeamLlmModel.team_id == team_id)
        .order_by(TeamLlmModel.sort_order, TeamLlmModel.id))).scalars().all())


def _model_info(row: TeamLlmModel, provider: str) -> dict:
    return {
        "id": row.id,
        "team_id": row.team_id,
        "provider": provider,
        "model_id": row.model_id,
        "display_name": row.display_name or row.model_id,
        "enabled": row.enabled,
        "is_default": row.is_default,
        "sort_order": row.sort_order,
    }


async def _provider_of_model(db: AsyncSession, row: TeamLlmModel) -> TeamLlmProvider | None:
    return await db.get(TeamLlmProvider, row.provider_id)


# ---------- 供应商连接 ----------
@router.get("/providers")
async def list_team_providers(
    trio: Annotated[tuple, Depends(require_team_perm("llm:use"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _, team, _role = trio
    rows = list((await db.execute(select(TeamLlmProvider).where(
        TeamLlmProvider.team_id == team.id))).scalars().all())
    saved = {row.provider: row for row in rows}
    models = await _model_rows(db, team.id)
    counts: dict[int, int] = {}
    for m in models:
        if m.enabled:
            counts[m.provider_id] = counts.get(m.provider_id, 0) + 1
    out = []
    for spec in PROVIDERS.values():
        row = saved.get(spec.id)
        out.append({
            **spec.as_dict(),
            **_connection_state(row, spec.id),
            "model_count": counts.get(row.id, 0) if row else 0,
        })
    return out


@router.put("/providers/{provider}")
async def save_team_provider(
    provider: str,
    body: ProviderIn,
    trio: Annotated[tuple, Depends(require_team_perm("llm:manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user, team, _role = trio
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "未知供应商")
    row = await _provider_row(db, team.id, spec.id)
    raw_key = (body.api_key or "").strip()
    if len(raw_key) > 512:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "API Key 过长")
    api_base = _valid_base(body.api_base)
    if spec.requires_base and not api_base:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请填写接口地址")
    if not raw_key and not _team_key(row):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请填写 API Key")
    if row is None:
        row = TeamLlmProvider(team_id=team.id, provider=spec.id, label=spec.label,
                              created_by=user.id)
    if raw_key:
        row.api_key_enc = encrypt_secret(raw_key)
        row.key_hint = "****" + raw_key[-4:]
    row.api_base = api_base
    row.protocol = body.protocol if body.protocol in spec.protocols else spec.protocols[0]
    row.enabled = body.enabled
    row.updated_at = datetime.now(timezone.utc)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": spec.id, "provider": spec.id, "label": spec.label,
            **_connection_state(row, spec.id), "model_count": 0}


@router.delete("/providers/{provider}")
async def delete_team_provider(
    provider: str,
    trio: Annotated[tuple, Depends(require_team_perm("llm:manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    force: bool = Query(False),
):
    _, team, _role = trio
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "未知供应商")
    row = await _provider_row(db, team.id, spec.id)
    if row is None:
        return {"id": spec.id, "provider": spec.id, "label": spec.label,
                **_connection_state(None, spec.id)}
    used = (await db.execute(select(func.count(TeamLlmModel.id)).where(
        TeamLlmModel.provider_id == row.id))).scalar() or 0
    if used and not force:
        raise HTTPException(status.HTTP_409_CONFLICT, "该供应商下仍有模型, 需先移除或强制断开")
    await db.execute(sa_update(TeamLlmModel).where(
        TeamLlmModel.provider_id == row.id).values(enabled=False, is_default=False))
    if force:
        await db.execute(sa_delete(TeamLlmModel).where(TeamLlmModel.provider_id == row.id))
    await db.delete(row)
    await db.commit()
    return {"id": spec.id, "provider": spec.id, "label": spec.label,
            **_connection_state(None, spec.id)}


@router.post("/providers/{provider}/discover")
async def discover_team_models(
    provider: str,
    body: DiscoverIn,
    trio: Annotated[tuple, Depends(require_team_perm("llm:manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _, team, _role = trio
    spec = spec_of(provider)
    row = await _provider_row(db, team.id, spec.id)
    key = (body.api_key or "").strip() or _team_key(row) or _platform_key(spec.id)
    base = _valid_base(body.api_base) or (row.api_base if row else "")
    protocol = body.protocol if body.protocol in spec.protocols else (
        row.protocol if row else spec.protocols[0])
    if not key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"请先连接 {spec.label}")
    res = await discover_models(provider=spec.id, api_key=key, api_base=base,
                                protocol=protocol)
    selected = {m.model_id for m in await _model_rows(db, team.id)
                if row and m.provider_id == row.id}
    return {**res, "selected_ids": sorted(selected)}


# ---------- 启用模型清单 ----------
@router.get("/providers/{provider}/models")
async def list_team_models(
    provider: str,
    trio: Annotated[tuple, Depends(require_team_perm("llm:manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _, team, _role = trio
    spec = spec_of(provider)
    row = await _provider_row(db, team.id, spec.id)
    if row is None:
        return []
    return [_model_info(m, spec.id) for m in await _model_rows(db, team.id)
            if m.provider_id == row.id]


@router.put("/providers/{provider}/models")
async def save_team_models(
    provider: str,
    body: ModelsIn,
    trio: Annotated[tuple, Depends(require_team_perm("llm:manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """以本次提交的清单为准: 新增/更新, 未提交的模型置为停用 (保留显示名称)."""
    _, team, _role = trio
    spec = spec_of(provider)
    prow = await _provider_row(db, team.id, spec.id)
    if prow is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"请先连接 {spec.label}")
    rows = {m.model_id: m for m in await _model_rows(db, team.id)
            if m.provider_id == prow.id}
    submitted: set[str] = set()
    for index, item in enumerate(body.models):
        model_id = (item.model_id or "").strip()[:128]
        if not model_id:
            continue
        submitted.add(model_id)
        row = rows.get(model_id)
        if row is None:
            row = TeamLlmModel(team_id=team.id, provider_id=prow.id, model_id=model_id)
        row.display_name = (item.display_name or "").strip()[:128]
        row.enabled = item.enabled
        row.sort_order = item.sort_order if item.sort_order else index
        db.add(row)
    for model_id, row in rows.items():
        if model_id not in submitted:
            row.enabled = False
            row.is_default = False
            db.add(row)
    await db.flush()
    defaults = (await db.execute(select(TeamLlmModel).where(
        TeamLlmModel.team_id == team.id, TeamLlmModel.is_default.is_(True)))).scalars().all()
    if not defaults:
        first = (await db.execute(select(TeamLlmModel).where(
            TeamLlmModel.team_id == team.id, TeamLlmModel.enabled.is_(True))
            .order_by(TeamLlmModel.sort_order, TeamLlmModel.id).limit(1))).scalar_one_or_none()
        if first is not None:
            first.is_default = True
            db.add(first)
    await db.commit()
    return [_model_info(m, spec.id) for m in await _model_rows(db, team.id)
            if m.provider_id == prow.id]


@router.patch("/models/{row_id}")
async def patch_team_model(
    row_id: int,
    body: ModelPatch,
    trio: Annotated[tuple, Depends(require_team_perm("llm:manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _, team, _role = trio
    row = await db.get(TeamLlmModel, row_id)
    if row is None or row.team_id != team.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模型不存在")
    if body.display_name is not None:
        row.display_name = body.display_name.strip()[:128]
    if body.enabled is not None:
        row.enabled = body.enabled
        if not body.enabled:
            row.is_default = False
    if body.sort_order is not None:
        row.sort_order = body.sort_order
    row.updated_at = datetime.now(timezone.utc)
    db.add(row)
    await db.commit()
    prow = await _provider_of_model(db, row)
    return _model_info(row, prow.provider if prow else "")


@router.put("/models/{row_id}/default")
async def set_default_model(
    row_id: int,
    trio: Annotated[tuple, Depends(require_team_perm("llm:manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _, team, _role = trio
    row = await db.get(TeamLlmModel, row_id)
    if row is None or row.team_id != team.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模型不存在")
    if not row.enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "停用的模型不能设为默认")
    await db.execute(sa_update(TeamLlmModel).where(
        TeamLlmModel.team_id == team.id).values(is_default=False))
    row.is_default = True
    db.add(row)
    await db.commit()
    prow = await _provider_of_model(db, row)
    return _model_info(row, prow.provider if prow else "")


@router.delete("/models/{row_id}")
async def delete_team_model(
    row_id: int,
    trio: Annotated[tuple, Depends(require_team_perm("llm:manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _, team, _role = trio
    row = await db.get(TeamLlmModel, row_id)
    if row is None or row.team_id != team.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模型不存在")
    row.is_default = False
    db.add(row)
    await db.flush()
    await db.delete(row)
    await db.commit()
    return {"detail": "已删除"}


@router.post("/models/{row_id}/reasoning")
async def model_reasoning(
    row_id: int,
    trio: Annotated[tuple, Depends(require_team_perm("llm:use"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _, team, _role = trio
    row = await db.get(TeamLlmModel, row_id)
    if row is None or row.team_id != team.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模型不存在")
    prow = await _provider_of_model(db, row)
    if prow is None:
        return {"levels": [], "default": ""}
    return await reasoning_options(prow.provider, row.model_id, prow.api_base)


# ---------- 用量 ----------
def _usage_totals(row) -> dict:
    return {
        "requests": int(row.requests or 0),
        "input_tokens": int(row.input_tokens or 0),
        "output_tokens": int(row.output_tokens or 0),
        "cached_read_tokens": int(row.cached_read_tokens or 0),
        "cached_write_tokens": int(row.cached_write_tokens or 0),
        "thought_tokens": int(row.thought_tokens or 0),
        "total_tokens": int(row.total_tokens or 0),
    }


def _usage_filter(team_id: int, start: datetime, end: datetime, user_id: int | None = None):
    conds = [LlmUsageEvent.team_id == team_id,
             LlmUsageEvent.created_at >= start,
             LlmUsageEvent.created_at < end]
    if user_id is not None:
        conds.append(LlmUsageEvent.user_id == user_id)
    return conds


@router.get("/usage")
async def team_usage(
    trio: Annotated[tuple, Depends(require_team_perm("llm:usage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    start: str = "",
    end: str = "",
    group_by: str = Query("user", pattern="^(user|model|day)$"),
):
    _, team, _role = trio
    if group_by == "user":
        return await _usage_by_user(db, team.id, start, end)
    if group_by == "model":
        return await _usage_by_model(db, team.id, start, end)
    return await _usage_by_day(db, team.id, start, end)


@router.get("/usage/me")
async def my_usage(
    trio: Annotated[tuple, Depends(require_team_perm("llm:use"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    start: str = "",
    end: str = "",
):
    _, team, _role = trio
    return await _usage_by_user(db, team.id, start, end, user_id=user.id)


def _range(start: str, end: str) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    try:
        begin = datetime.fromisoformat(start).replace(tzinfo=timezone.utc) if start else now - timedelta(days=30)
        finish = datetime.fromisoformat(end).replace(tzinfo=timezone.utc) + timedelta(days=1) if end else now + timedelta(days=1)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "日期格式应为 YYYY-MM-DD")
    return begin, finish


async def _usage_by_user(db: AsyncSession, team_id: int, start: str, end: str,
                         user_id: int | None = None) -> dict:
    begin, finish = _range(start, end)
    conds = _usage_filter(team_id, begin, finish, user_id)
    q = (select(
        LlmUsageEvent.user_id,
        User.display_name,
        User.username,
        LlmUsageEvent.provider,
        LlmUsageEvent.model,
        func.max(LlmUsageEvent.model_name).label("model_name"),
        func.count(LlmUsageEvent.id).label("requests"),
        func.coalesce(func.sum(LlmUsageEvent.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(LlmUsageEvent.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(LlmUsageEvent.cached_read_tokens), 0).label("cached_read_tokens"),
        func.coalesce(func.sum(LlmUsageEvent.cached_write_tokens), 0).label("cached_write_tokens"),
        func.coalesce(func.sum(LlmUsageEvent.thought_tokens), 0).label("thought_tokens"),
        func.coalesce(func.sum(LlmUsageEvent.total_tokens), 0).label("total_tokens"),
    ).join(User, User.id == LlmUsageEvent.user_id).where(*conds)
     .group_by(LlmUsageEvent.user_id, User.display_name, User.username,
               LlmUsageEvent.provider, LlmUsageEvent.model)
     .order_by(func.sum(LlmUsageEvent.total_tokens).desc()))
    rows = []
    for r in (await db.execute(q)).all():
        rows.append({
            "user_id": r.user_id,
            "user_name": r.display_name or r.username,
            "provider": r.provider,
            "model": r.model,
            "model_name": r.model_name or r.model,
            **_usage_totals(r),
        })
    return {"group_by": "user", "rows": rows}


async def _usage_by_model(db: AsyncSession, team_id: int, start: str, end: str) -> dict:
    begin, finish = _range(start, end)
    conds = _usage_filter(team_id, begin, finish)
    q = (select(
        LlmUsageEvent.provider,
        LlmUsageEvent.model,
        func.max(LlmUsageEvent.model_name).label("model_name"),
        func.count(LlmUsageEvent.id).label("requests"),
        func.count(func.distinct(LlmUsageEvent.user_id)).label("users"),
        func.coalesce(func.sum(LlmUsageEvent.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(LlmUsageEvent.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(LlmUsageEvent.cached_read_tokens), 0).label("cached_read_tokens"),
        func.coalesce(func.sum(LlmUsageEvent.cached_write_tokens), 0).label("cached_write_tokens"),
        func.coalesce(func.sum(LlmUsageEvent.thought_tokens), 0).label("thought_tokens"),
        func.coalesce(func.sum(LlmUsageEvent.total_tokens), 0).label("total_tokens"),
    ).where(*conds)
     .group_by(LlmUsageEvent.provider, LlmUsageEvent.model)
     .order_by(func.sum(LlmUsageEvent.total_tokens).desc()))
    rows = []
    for r in (await db.execute(q)).all():
        rows.append({
            "provider": r.provider,
            "model": r.model,
            "model_name": r.model_name or r.model,
            "users": int(r.users or 0),
            **_usage_totals(r),
        })
    return {"group_by": "model", "rows": rows}


async def _usage_by_day(db: AsyncSession, team_id: int, start: str, end: str) -> dict:
    begin, finish = _range(start, end)
    day = func.date(LlmUsageEvent.created_at)
    q = (select(
        day.label("day"),
        func.count(LlmUsageEvent.id).label("requests"),
        func.coalesce(func.sum(LlmUsageEvent.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(LlmUsageEvent.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(LlmUsageEvent.cached_read_tokens), 0).label("cached_read_tokens"),
        func.coalesce(func.sum(LlmUsageEvent.cached_write_tokens), 0).label("cached_write_tokens"),
        func.coalesce(func.sum(LlmUsageEvent.thought_tokens), 0).label("thought_tokens"),
        func.coalesce(func.sum(LlmUsageEvent.total_tokens), 0).label("total_tokens"),
    ).where(*_usage_filter(team_id, begin, finish))
     .group_by(day).order_by(day))
    rows = [{"day": str(r.day), **_usage_totals(r)} for r in (await db.execute(q)).all()]
    return {"group_by": "day", "rows": rows}
