"""工作台: 返回指定团队的计数 / KPI / 最近任务 / 高频错误码 (默认合并我的团队)."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import DashboardResponse, TaskInfo
from app.core.database import get_db
from app.core.deps import get_current_user, is_platform_admin
from app.models.task import Task, TaskType
from app.models.team import Team, TeamMember
from app.models.user import User

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardResponse)
async def dashboard(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    team_id: int | None = None,
):
    """团队范围聚合: team_id 指定当前团队, 未指定时合并当前用户所属团队."""
    if team_id is not None:
        member = (await db.execute(select(TeamMember).where(
            TeamMember.team_id == team_id, TeamMember.user_id == user.id))).scalar_one_or_none()
        if not member and not is_platform_admin(user):
            raise HTTPException(http_status.HTTP_403_FORBIDDEN, "未加入该团队")
        team_ids = [team_id]
    else:
        team_ids = list((await db.execute(
            select(TeamMember.team_id).where(TeamMember.user_id == user.id))).scalars().all())

    scope = Task.team_id.in_(team_ids or [0])

    # 任务计数: 按类型分组, 与列表页口径一致
    type_rows = (await db.execute(
        select(Task.type, func.count()).where(scope).group_by(Task.type))).all()
    task_types = {t.value: 0 for t in TaskType}
    for tp, cnt in type_rows:
        task_types[tp.value] = cnt
    tasks_total = sum(task_types.values())

    # KPI 与 Top 错误码: 在事件表按团队范围聚合 (SQL, 免加载)
    from app.models.analyze import ParsedEventRecord
    kpi = {"total": 0, "critical": 0, "error": 0, "warning": 0}
    top_codes: list[dict] = []
    if tasks_total:
        rows = (await db.execute(
            select(ParsedEventRecord.severity, func.count())
            .join(Task, ParsedEventRecord.task_id == Task.id)
            .where(scope)
            .group_by(ParsedEventRecord.severity))).all()
        for sev, cnt in rows:
            kpi["total"] += cnt
            if sev in kpi:
                kpi[sev] = cnt
        code_rows = (await db.execute(
            select(ParsedEventRecord.error_code, func.count())
            .join(Task, ParsedEventRecord.task_id == Task.id)
            .where(scope, ParsedEventRecord.error_code != "")
            .group_by(ParsedEventRecord.error_code)
            .order_by(func.count().desc()).limit(8))).all()
        top_codes = [{"name": c, "value": n} for c, n in code_rows]

    recent_tasks = (await db.execute(
        select(Task).where(scope).order_by(Task.created_at.desc()).limit(10))).scalars().all()

    recent: list[TaskInfo] = []
    team_names = dict((await db.execute(
        select(Team.id, Team.name)
        .where(Team.id.in_([t.team_id for t in recent_tasks] or [0])))).all())
    for t in recent_tasks:
        creator = (await db.execute(select(User.display_name, User.username)
                                    .where(User.id == t.user_id))).first()
        recent.append(TaskInfo(
            id=t.id, type=t.type, status=t.status, filename=t.filename,
            progress=t.progress, error=t.error, created_at=t.created_at,
            finished_at=t.finished_at, name=t.name, parser_type=t.parser_type,
            team_id=t.team_id, log_size=t.log_size, event_count=t.event_count,
            creator_name=(creator[0] or creator[1]) if creator else "",
            team_name=team_names.get(t.team_id, "") if t.team_id else ""))

    return DashboardResponse(
        teams=len(team_ids), tasks=tasks_total, task_types=task_types,
        kpi=kpi, recent_tasks=recent, top_codes=top_codes)
