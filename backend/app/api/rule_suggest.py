"""智能巡检规则推荐 API: 触发 / SSE 流式订阅 / 查询已存结果.

对应巡检详情页「智能巡检规则推荐」: 让大模型评估当前规则完善程度并提出可新增的规则项,
用户可编辑后一键接纳跳转规则管理页新增。结果与任务绑定, 刷新/切页签可复看, 不写入报告。
"""
import asyncio
import json
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.analysis import (_ensure_task_perm, _load_visible_task,
                              _team_id_of_task)
from app.api.schemas import RuleSuggestionInfo, RuleSuggestStartResponse
from app.core.audit import log_audit
from app.core.database import get_db
from app.core.deps import (get_current_user, get_current_user_or_query, get_redis)
from app.events.event_bus import EventBus
from app.models.rule_suggestion import RuleSuggestion
from app.models.task import Task, TaskType
from app.models.user import User
from app.queue.task_queue import TaskQueue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tasks", tags=["rule-suggest"])


@router.post("/{task_id}/rule-suggestions", response_model=RuleSuggestStartResponse)
async def start_rule_suggest(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    redis: Annotated[object, Depends(get_redis)],
):
    """触发规则推荐: 抢锁 → INCR run_id → 入队 rule_suggest → 前端订阅 SSE."""
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    if task.type != TaskType.inspection:
        raise HTTPException(status.HTTP_409_CONFLICT, "仅智能巡检任务支持规则推荐")
    if task.status != "done":
        raise HTTPException(status.HTTP_409_CONFLICT, "任务尚未完成, 无法生成规则推荐")
    await _ensure_task_perm(db, user, task, "task:llm")

    if await redis.exists(f"suggest:lock:{task_id}"):
        raise HTTPException(status.HTTP_409_CONFLICT, "已有规则推荐生成中, 请稍候")
    run_id = str(await redis.incr("suggest:run:seq"))
    queue = TaskQueue(redis)
    await queue.enqueue(str(task_id), "rule_suggest", {"run_id": run_id})
    log_audit(db, user, "生成规则推荐", task.name or task.filename,
              team_id=(await _team_id_of_task(db, task)), detail={"run_id": run_id})
    await db.commit()
    return RuleSuggestStartResponse(run_id=run_id)


@router.get("/{task_id}/rule-suggestions", response_model=RuleSuggestionInfo)
async def get_rule_suggestion(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """查询已持久化的规则推荐结果 (无则 404)."""
    await _load_visible_task(db, user, task_id)
    row = (await db.execute(
        select(RuleSuggestion).where(RuleSuggestion.task_id == task_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "尚未生成规则推荐")
    return RuleSuggestionInfo(
        task_id=row.task_id, assessment=row.assessment, items=row.items or [],
        model=row.model, updated_at=row.updated_at)


@router.get("/{task_id}/rule-suggestions/events")
async def rule_suggest_events(
    task_id: str,
    request: Request,
    run_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user_or_query)],
    redis: Annotated[object, Depends(get_redis)],
):
    """规则推荐流式 SSE: 订阅 {task_id}:suggest:{run_id} 独立通道 (?token= 认证)."""
    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "任务 ID 非法")
    await _load_visible_task(db, user, tid)

    key = f"{task_id}:suggest:{run_id}"
    bus = EventBus(redis)
    stop = asyncio.Event()

    async def event_generator():
        for event in await bus.replay(key):
            yield {"event": event.get("type", "message"),
                   "data": json.dumps(event, ensure_ascii=False)}
            if event.get("type") in ("done", "error"):
                stop.set()
        if stop.is_set():
            return

        live: asyncio.Queue = asyncio.Queue()

        def on_event(event: dict):
            live.put_nowait(event)
            if event.get("type") in ("done", "error"):
                stop.set()

        subscribe_task = asyncio.create_task(bus.subscribe(key, on_event, stop))
        try:
            while not stop.is_set():
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(live.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
                    continue
                yield {"event": event.get("type", "message"),
                       "data": json.dumps(event, ensure_ascii=False)}
        finally:
            stop.set()
            subscribe_task.cancel()
            try:
                await subscribe_task
            except (asyncio.CancelledError, Exception):
                pass

    from sse_starlette.sse import EventSourceResponse
    return EventSourceResponse(event_generator())