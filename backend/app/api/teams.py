"""团队 API: 列表/创建/详情/编辑 + 邀请/申请 + 成员角色管理 + 团队任务列表 (团队级 RBAC).

对齐 witty-log-analyzer 团队体系: 双角色 (team_admin/member)、邀请与申请审批流.
创建者与团队管理员权限已合一, Team.owner_user_id 仅用于标识创建人 (不可被移除).
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (InviteRequest, MemberInfo, MemberRoleUpdate, NoticeInfo,
                             TaskInfo, TaskListPage, TeamCreate, TeamDetail, TeamInfo,
                             TeamUpdate)
from app.core.audit import log_audit
from app.core.database import get_db
from app.core.deps import (ensure_team_perm, get_current_user, load_team_role,
                           require_team_perm)
from app.models.task import Task, TaskType
from app.models.team import Team, TeamMember, TeamNotice
from app.models.user import User

router = APIRouter(prefix="/teams", tags=["teams"])

VALID_ROLES = {"member", "team_admin"}  # 团队内双角色: 管理员 / 普通用户
SPARK_BUCKETS = 16


async def _team_aggregates(db: AsyncSession, team_ids: list[int]) -> dict[int, dict]:
    """每团队聚合: 成员数/任务数."""
    out = {tid: {"members": 0, "tasks": 0} for tid in team_ids}
    if not team_ids:
        return out
    for row in (await db.execute(
        select(TeamMember.team_id, func.count())
        .where(TeamMember.team_id.in_(team_ids)).group_by(TeamMember.team_id))).all():
        out[row[0]]["members"] = row[1]
    for row in (await db.execute(
        select(Task.team_id, func.count())
        .where(Task.team_id.in_(team_ids)).group_by(Task.team_id))).all():
        out[row[0]]["tasks"] = row[1]
    return out


def _team_info(team: Team, agg: dict, my_role: str | None) -> TeamInfo:
    return TeamInfo(
        id=team.id, name=team.name, desc=team.desc,
        owner_user_id=team.owner_user_id, created_at=team.created_at,
        my_role=my_role, member_count=agg["members"], task_count=agg["tasks"])


@router.get("", response_model=list[TeamInfo])
async def list_teams(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """全部团队概览 (非成员也可看, 供「申请加入」); my_role 标识我的角色."""
    teams = (await db.execute(select(Team).order_by(Team.created_at.desc()))).scalars().all()
    my_roles = {m.team_id: m.role for m in (await db.execute(
        select(TeamMember).where(TeamMember.user_id == user.id))).scalars()}
    aggs = await _team_aggregates(db, [t.id for t in teams])
    return [_team_info(t, aggs.get(t.id, {"members": 0, "tasks": 0}),
                       my_roles.get(t.id)) for t in teams]


@router.post("", response_model=TeamInfo, status_code=status.HTTP_201_CREATED)
async def create_team(
    body: TeamCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """创建团队: 同事务建 Team + 管理员成员 (创建者即团队管理员)."""
    if (await db.execute(select(Team).where(Team.name == body.name))).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "团队名已存在")
    team = Team(name=body.name, desc=body.desc, owner_user_id=user.id)
    db.add(team)
    await db.flush()
    db.add(TeamMember(team_id=team.id, user_id=user.id, role="team_admin"))
    log_audit(db, user, "创建团队", f"{body.name}（你自动成为团队管理员）", team_id=team.id)
    await db.commit()
    return TeamInfo(id=team.id, name=team.name, desc=team.desc,
                    owner_user_id=team.owner_user_id, created_at=team.created_at,
                    my_role="team_admin", member_count=1, task_count=0)


@router.get("/{team_id}", response_model=TeamDetail)
async def team_detail(
    team_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """团队详情: 成员/待审批申请 (申请仅团队管理员可见)."""
    team = (await db.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "团队不存在")
    _, my_role = await load_team_role(db, user, team_id)

    members = [MemberInfo(
        user_id=u.id, name=u.display_name or u.username, email=u.email,
        title=u.title, dept=u.dept, avatar_hue=u.avatar_hue, role=m.role)
        for m, u in (await db.execute(
            select(TeamMember, User).join(User, TeamMember.user_id == User.id)
            .where(TeamMember.team_id == team_id)
            .order_by(TeamMember.created_at))).all()]

    pending_applies: list[NoticeInfo] = []
    if my_role == "team_admin":
        pending_applies = await _notice_infos(db, select(TeamNotice).where(
            TeamNotice.team_id == team_id, TeamNotice.type == "apply",
            TeamNotice.status == "pending"))

    agg = (await _team_aggregates(db, [team_id])).get(team_id, {"members": 0, "tasks": 0})
    return TeamDetail(
        id=team.id, name=team.name, desc=team.desc, owner_user_id=team.owner_user_id,
        created_at=team.created_at, my_role=my_role,
        member_count=agg["members"], task_count=agg["tasks"],
        members=members, pending_applies=pending_applies)


async def _spark_of(db: AsyncSession, task_id) -> list[dict]:
    """单任务 16 桶时序概览 (SQL width_bucket, 免全量加载)."""
    rows = (await db.execute(text("""
        WITH t AS (SELECT min(ts) mn, max(ts) mx FROM parsed_events WHERE task_id = :tid)
        SELECT width_bucket(pe.ts, t.mn, t.mx + 1, :n) AS b,
               count(*) AS total,
               count(*) FILTER (WHERE pe.severity = 'critical') AS critical,
               count(*) FILTER (WHERE pe.severity = 'error') AS error
        FROM parsed_events pe, t
        WHERE pe.task_id = :tid
        GROUP BY 1 ORDER BY 1
    """), {"tid": str(task_id), "n": SPARK_BUCKETS})).all()
    if not rows:
        return []
    bounds = (await db.execute(text(
        "SELECT min(ts) mn, max(ts) mx FROM parsed_events WHERE task_id = :tid"),
        {"tid": str(task_id)})).one()
    mn, mx = int(bounds.mn), int(bounds.mx)
    step = max(mx - mn, 60_000) / SPARK_BUCKETS
    by_idx = {int(r[0]): (int(r[1]), int(r[2]), int(r[3])) for r in rows}
    from datetime import datetime
    out = []
    for i in range(SPARK_BUCKETS):
        t = int(mn + i * step)
        lt = datetime.fromtimestamp(t / 1000)
        total, crit, err = by_idx.get(i + 1, (0, 0, 0))  # width_bucket 从 1 起
        out.append({"t": t,
                    "label": f"{lt.hour:02d}:{lt.minute:02d}",
                    "total": total, "critical": crit, "error": err})
    return out


@router.get("/{team_id}/tasks", response_model=TaskListPage)
async def list_team_tasks(
    team_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    status_filter: str | None = None,
    task_type: TaskType | None = None,
    parser_type: str | None = None,
    kw: str | None = None,
    page: int = 1,
    page_size: int = 10,
):
    """团队任务 (服务端分页/筛选 + 每任务 spark)."""
    await ensure_team_perm(db, user, team_id, "task:view")
    team_name = (await db.execute(select(Team.name)
                                 .where(Team.id == team_id))).scalar_one_or_none() or ""

    query = select(Task).where(Task.team_id == team_id)
    if status_filter:
        query = query.where(Task.status == status_filter)
    if task_type:
        query = query.where(Task.type == task_type)
    if parser_type:
        query = query.where(Task.parser_type == parser_type)
    if kw:
        like = f"%{kw}%"
        query = query.where(or_(Task.name.ilike(like), Task.filename.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    tasks = (await db.execute(query.order_by(Task.created_at.desc())
                              .offset((page - 1) * page_size).limit(page_size))).scalars().all()

    items, sparks = [], {}
    for t in tasks:
        creator = (await db.execute(select(User.display_name, User.username)
                                    .where(User.id == t.user_id))).first()
        items.append(TaskInfo(
            id=t.id, type=t.type, status=t.status, filename=t.filename,
            progress=t.progress, error=t.error, created_at=t.created_at,
            finished_at=t.finished_at, name=t.name, parser_type=t.parser_type,
            team_id=t.team_id, log_size=t.log_size, event_count=t.event_count,
            creator_name=(creator[0] or creator[1]) if creator else "",
            team_name=team_name))
        sparks[str(t.id)] = await _spark_of(db, t.id)
    return TaskListPage(total=total, items=items, sparks=sparks)


@router.put("/{team_id}", response_model=TeamInfo)
async def update_team(
    team_id: int,
    body: TeamUpdate,
    trio: Annotated[tuple, Depends(require_team_perm("team:role"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """编辑团队信息 (团队管理员)."""
    user, team, _ = trio
    if body.name != team.name:
        if (await db.execute(select(Team).where(Team.name == body.name))).scalar_one_or_none():
            raise HTTPException(status.HTTP_409_CONFLICT, "团队名已存在")
    team.name, team.desc = body.name, body.desc
    log_audit(db, user, "编辑团队", body.name, team_id=team_id)
    await db.commit()
    agg = (await _team_aggregates(db, [team_id])).get(team_id, {"members": 0, "tasks": 0})
    _, my_role = await load_team_role(db, user, team_id)
    return _team_info(team, agg, my_role)


async def _notice_infos(db: AsyncSession, query) -> list[NoticeInfo]:
    """TeamNotice 行 → NoticeInfo (join 团队名/发起人/处理人)."""
    rows = (await db.execute(
        query)).scalars().all()
    out: list[NoticeInfo] = []
    for n in rows:
        team_name = (await db.execute(select(Team.name).where(Team.id == n.team_id))).scalar_one_or_none() or ""
        from_name = (await db.execute(select(User.display_name, User.username)
                                      .where(User.id == n.from_user_id))).first()
        to_name = (await db.execute(select(User.display_name, User.username)
                                    .where(User.id == n.to_user_id))).first()
        out.append(NoticeInfo(
            id=n.id, type=n.type, team_id=n.team_id, team_name=team_name,
            from_user_id=n.from_user_id,
            from_user_name=(from_name[0] or from_name[1]) if from_name else "",
            to_user_id=n.to_user_id,
            to_user_name=(to_name[0] or to_name[1]) if to_name else "",
            role=n.role, status=n.status, created_at=n.created_at))
    return out


@router.post("/{team_id}/invite", status_code=status.HTTP_201_CREATED)
async def invite_member(
    team_id: int,
    body: InviteRequest,
    trio: Annotated[tuple, Depends(require_team_perm("team:invite"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """邀请成员 (团队管理员): 生成 pending 邀请, 待对方在通知中心确认."""
    user, team, _ = trio
    if body.role not in VALID_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"角色必须是: {sorted(VALID_ROLES)}")
    target = (await db.execute(select(User).where(User.id == body.user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if (await db.execute(select(TeamMember).where(
            TeamMember.team_id == team_id, TeamMember.user_id == body.user_id))).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "该用户已是团队成员")
    dup = (await db.execute(select(TeamNotice).where(
        TeamNotice.type == "invite", TeamNotice.team_id == team_id,
        TeamNotice.to_user_id == body.user_id, TeamNotice.status == "pending"))).scalar_one_or_none()
    if dup is None:  # 重复 pending 邀请幂等跳过
        db.add(TeamNotice(type="invite", team_id=team_id, from_user_id=user.id,
                          to_user_id=body.user_id, role=body.role))
    log_audit(db, user, "邀请加入团队",
              f"{team.name} / {target.display_name or target.username}（对方右上角确认）",
              team_id=team_id)
    await db.commit()
    return {"detail": "邀请已发送"}


@router.post("/{team_id}/apply", status_code=status.HTTP_201_CREATED)
async def apply_join(
    team_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """申请加入团队 (任意登录用户): 生成 pending 申请, 待团队管理员审批."""
    team = (await db.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "团队不存在")
    if (await db.execute(select(TeamMember).where(
            TeamMember.team_id == team_id, TeamMember.user_id == user.id))).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "你已是团队成员")
    dup = (await db.execute(select(TeamNotice).where(
        TeamNotice.type == "apply", TeamNotice.team_id == team_id,
        TeamNotice.from_user_id == user.id, TeamNotice.status == "pending"))).scalar_one_or_none()
    if dup is None:
        # 默认审批人: 团队创建人 (其亦为团队管理员)
        db.add(TeamNotice(type="apply", team_id=team_id, from_user_id=user.id,
                          to_user_id=team.owner_user_id, role="member"))
    log_audit(db, user, "申请加入团队", team.name, team_id=team_id)
    await db.commit()
    return {"detail": "申请已提交"}


@router.put("/{team_id}/members/{member_user_id}")
async def update_member_role(
    team_id: int,
    member_user_id: int,
    body: MemberRoleUpdate,
    trio: Annotated[tuple, Depends(require_team_perm("team:role"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """调整成员角色 (团队管理员); 团队创建人不可被调整."""
    user, team, _ = trio
    if body.role not in VALID_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"角色必须是: {sorted(VALID_ROLES)}")
    member = (await db.execute(select(TeamMember).where(
        TeamMember.team_id == team_id, TeamMember.user_id == member_user_id))).scalar_one_or_none()
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "成员不存在")
    if member_user_id == team.owner_user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "团队创建人角色不可调整")
    member.role = body.role
    target = (await db.execute(select(User).where(User.id == member_user_id))).scalar_one()
    log_audit(db, user, "调整成员角色",
              f"{team.name} / {target.display_name or target.username} → {body.role}",
              team_id=team_id)
    await db.commit()
    return {"detail": "角色已更新"}


@router.delete("/{team_id}/members/{member_user_id}")
async def remove_member(
    team_id: int,
    member_user_id: int,
    trio: Annotated[tuple, Depends(require_team_perm("team:remove"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """移除团队成员 (团队管理员); 团队创建人不可被移除, 也不可移除自己."""
    user, team, _ = trio
    member = (await db.execute(select(TeamMember).where(
        TeamMember.team_id == team_id, TeamMember.user_id == member_user_id))).scalar_one_or_none()
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "成员不存在")
    if member_user_id == team.owner_user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "团队创建人不可被移除")
    if member_user_id == user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "不可移除自己")
    await db.delete(member)
    target = (await db.execute(select(User).where(User.id == member_user_id))).scalar_one()
    log_audit(db, user, "移除团队成员",
              f"{team.name} / {target.display_name or target.username}", team_id=team_id)
    await db.commit()
    return {"detail": "成员已移除"}
