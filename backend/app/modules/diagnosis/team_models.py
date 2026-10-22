"""解析用户可用的团队模型与凭据 (团队级配置, 个人只保留选择)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.deps import load_team_role
from app.core.team_rbac import team_role_has_perm
from app.models.llm import TeamLlmModel, TeamLlmProvider
from app.models.team import Team
from app.models.user import User
from app.modules.agent.providers import spec_of

settings = get_settings()


@dataclass
class ResolvedModel:
    team_id: int = 0
    team_name: str = ""
    model_row_id: int = 0
    provider: str = ""
    model: str = ""
    model_name: str = ""
    api_base: str = ""
    protocol: str = ""
    api_key: str = ""
    provider_row_id: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _platform_key(provider: str) -> str:
    if spec_of(provider).id != "deepseek":
        return ""
    return settings.AGENT_API_KEY or ""


async def team_models(db: AsyncSession, team_id: int) -> list[tuple[TeamLlmModel, TeamLlmProvider]]:
    """团队启用且供应商启用的模型."""
    rows = (await db.execute(
        select(TeamLlmModel, TeamLlmProvider)
        .join(TeamLlmProvider, TeamLlmProvider.id == TeamLlmModel.provider_id)
        .where(TeamLlmModel.team_id == team_id,
               TeamLlmModel.enabled.is_(True),
               TeamLlmProvider.enabled.is_(True))
        .order_by(TeamLlmModel.sort_order, TeamLlmModel.id))).all()
    return [(m, p) for m, p in rows]


async def default_model_row(db: AsyncSession, team_id: int) -> tuple[TeamLlmModel, TeamLlmProvider] | None:
    rows = await team_models(db, team_id)
    if not rows:
        return None
    for m, p in rows:
        if m.is_default:
            return m, p
    return rows[0]


async def resolve_team_model(db: AsyncSession, user: User, team_id: int,
                             model_row_id: int | None = None) -> ResolvedModel:
    team, role = await load_team_role(db, user, team_id)
    if team is None:
        return ResolvedModel(error="团队不存在")
    if role is None:
        return ResolvedModel(error="非团队成员, 无权使用该团队模型")
    if not team_role_has_perm(role, "llm:use"):
        return ResolvedModel(error="当前团队角色无权使用大模型")
    picked: tuple[TeamLlmModel, TeamLlmProvider] | None = None
    if model_row_id:
        row = await db.get(TeamLlmModel, model_row_id)
        if row is None or row.team_id != team_id:
            return ResolvedModel(error="模型不存在或不属于该团队")
        if not row.enabled:
            return ResolvedModel(error="该模型已被团队停用")
        prow = await db.get(TeamLlmProvider, row.provider_id)
        if prow is None or not prow.enabled:
            return ResolvedModel(error="该模型的供应商已被团队停用")
        picked = (row, prow)
    else:
        found = await default_model_row(db, team_id)
        if found is None:
            return ResolvedModel(error="团队尚未配置可用模型")
        picked = found
    row, prow = picked
    key = decrypt_secret(prow.api_key_enc) if prow.api_key_enc else ""
    key = key or _platform_key(prow.provider)
    if not key:
        return ResolvedModel(
            team_id=team.id, team_name=team.name,
            error="团队尚未配置该供应商的 API Key")
    spec = spec_of(prow.provider)
    return ResolvedModel(
        team_id=team.id, team_name=team.name,
        model_row_id=row.id, provider=spec.id, model=row.model_id,
        model_name=row.display_name or row.model_id,
        api_base=prow.api_base or spec.default_base,
        protocol=prow.protocol or spec.protocols[0],
        api_key=key, provider_row_id=prow.id,
    )


async def user_teams(db: AsyncSession, user: User, *, all_for_admin: bool = False) -> list[Team]:
    """用户可见团队; 平台管理员可见全部."""
    from app.core.deps import is_platform_admin
    from app.models.team import TeamMember

    if all_for_admin and is_platform_admin(user):
        return list((await db.execute(select(Team).order_by(Team.id))).scalars().all())
    rows = (await db.execute(
        select(Team).join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user.id).order_by(Team.id))).scalars().all()
    return list(rows)
