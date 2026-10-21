"""分析 Worker 入口: 消费 Redis Stream 队列, 并发执行诊断/巡检流水线.

启动: python -m app.worker  (或 uvicorn 不适用, 这是独立进程)
"""
import os

# Worker 进程连接池独立收紧: 多进程总和须控制在 PostgreSQL max_connections 之内
os.environ.setdefault("DB_POOL_SIZE", "5")
os.environ.setdefault("DB_MAX_OVERFLOW", "5")

import asyncio
import logging
import signal
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis
from sqlalchemy import func, or_, update

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal, Base, engine
from app.events.event_bus import EventBus
from app.models.task import Task, TaskStatus
from app.modules.analyze.llm_report import run_llm_report
from app.modules.analyze.pipeline import run_analyze
from app.modules.diagnosis.pipeline import run_diagnosis
from app.modules.inspection.pipeline import run_inspection
from app.modules.inspection.rule_suggest import run_rule_suggest
from app.queue.task_queue import TaskQueue

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker")
settings = get_settings()

PIPELINES = {
    "diagnosis": run_diagnosis,
    "inspection": run_inspection,
    "analyze": run_analyze,
}

shutdown = asyncio.Event()


def consumer_name() -> str:
    """消费组内唯一标识: 环境变量优先, 留空则 hostname-pid (多副本不冲突)."""
    return settings.WORKER_CONSUMER_NAME.strip() or f"{socket.gethostname()}-{os.getpid()}"


async def recover_interrupted(redis: Redis) -> None:
    """进程重启后, 将超时未更新的 running 任务标记为 interrupted.

    只处理 started_at 早于 pending 超时的任务, 避免多副本启动时误伤其它
    Worker 正在执行的任务; 对应消息由 reclaim_stale 重新投递执行.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(milliseconds=settings.WORKER_PENDING_TIMEOUT_MS)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(Task)
            .where(
                Task.status == TaskStatus.running,
                or_(Task.started_at.is_(None), Task.started_at < cutoff),
            )
            .values(status=TaskStatus.interrupted)
        )
        count = result.rowcount or 0
        if count:
            await db.commit()
            logger.warning("标记 %d 个超时中断任务为 interrupted (可重新下发)", count)


async def make_cancel_checker(redis: Redis, task_id: str):
    async def is_cancelled() -> bool:
        return bool(await redis.exists(f"cancel:{task_id}"))
    return is_cancelled


def _retry_key(task_id: str, task_type: str) -> str:
    return f"retry:{task_id}:{task_type}"


async def mark_failed(task_id: str, error: str) -> None:
    """仅将未结束的任务置为失败, 不覆盖用户已取消/已完成的状态."""
    try:
        tid = uuid.UUID(task_id)
    except (TypeError, ValueError):
        return
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Task)
            .where(
                Task.id == tid,
                Task.status.in_((TaskStatus.pending, TaskStatus.running, TaskStatus.interrupted)),
            )
            .values(status=TaskStatus.failed, error=error[:2000], finished_at=func.now())
        )
        await db.commit()


async def _handle_failure(queue: TaskQueue, bus: EventBus, redis: Redis,
                          task_id: str, task_type: str, payload: dict, exc: Exception) -> None:
    """处理异常: 有限次重新入队; 超过上限则标记失败, 避免无限重试."""
    key = _retry_key(task_id, task_type)
    try:
        attempts = await redis.incr(key)
        if attempts == 1:
            await redis.expire(key, 3600)
    except Exception:
        logger.exception("任务 %s 重试计数失败", task_id)
        return

    if attempts <= settings.WORKER_RETRY_MAX:
        try:
            await queue.enqueue(task_id, task_type, payload)
            logger.warning("任务 %s(%s) 处理异常, 第 %d 次重新入队", task_id, task_type, attempts)
            return
        except Exception:
            logger.exception("任务 %s 重新入队失败", task_id)

    if task_type in PIPELINES:
        await mark_failed(task_id, f"任务处理异常: {exc}")
        try:
            await bus.publish(task_id, "error", {"message": str(exc)[:2000]})
            await bus.publish(task_id, "done", {"status": "failed"})
        except Exception:
            logger.exception("任务 %s 失败事件推送异常", task_id)
    await redis.delete(key)


async def handle_message(queue: TaskQueue, bus: EventBus, redis: Redis, msg_id: str,
                         task_id: str, task_type: str, payload: dict) -> None:
    try:
        cancelled = await make_cancel_checker(redis, task_id)
        if task_type == "llm":
            # 按需 LLM 报告: 独立签名, run_id 取自消息 payload
            run_id = str(payload.get("run_id") or uuid.uuid4())
            await run_llm_report(uuid.UUID(task_id), run_id, bus, cancelled)
        elif task_type == "rule_suggest":
            # 智能巡检规则推荐: 独立签名, run_id 取自消息 payload
            run_id = str(payload.get("run_id") or uuid.uuid4())
            await run_rule_suggest(uuid.UUID(task_id), run_id, bus, cancelled)
        else:
            pipeline = PIPELINES.get(task_type)
            if pipeline is None:
                raise RuntimeError(f"未知任务类型: {task_type}")
            await pipeline(uuid.UUID(task_id), bus, cancelled)
        await redis.delete(f"cancel:{task_id}")
        await redis.delete(_retry_key(task_id, task_type))
    except asyncio.CancelledError:
        # 停机取消: 不 ack, 消息留在 PEL 由后续回收逻辑重跑
        raise
    except Exception as e:
        logger.exception("任务 %s 处理异常", task_id)
        await _handle_failure(queue, bus, redis, task_id, task_type, payload, e)
    await queue.ack(msg_id)


async def main() -> None:
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    queue = TaskQueue(redis)
    bus = EventBus(redis)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await queue.ensure_group()
    await recover_interrupted(redis)

    consumer = consumer_name()
    semaphore = asyncio.Semaphore(settings.WORKER_CONCURRENCY)
    # 在途任务上限: 消费速度不会无限超过执行速度, 避免任务对象无界堆积
    max_inflight = max(settings.WORKER_CONCURRENCY * 2, settings.WORKER_CONCURRENCY + 1)
    inflight: set[asyncio.Task] = set()
    logger.info("Worker 启动: consumer=%s, 并发=%d", consumer, settings.WORKER_CONCURRENCY)

    # 调度器: 巡检定时触发 + 指标分区维护
    from app.worker.scheduler import start_scheduler
    await start_scheduler(redis)

    def dispatch(messages: list[tuple]) -> None:
        for msg_id, task_id, task_type, payload in messages:
            async def run(m_id=msg_id, t_id=task_id, t_type=task_type, p=payload):
                async with semaphore:
                    await handle_message(queue, bus, redis, m_id, t_id, t_type, p)

            task = asyncio.create_task(run())
            inflight.add(task)
            task.add_done_callback(inflight.discard)

    # 启动即回收上次崩溃遗留的消息, 交给当前 Worker 重新执行
    try:
        dispatch(await queue.reclaim_stale(consumer))
    except Exception:
        logger.exception("启动回收 pending 任务失败")

    async def consume_loop():
        last_reclaim = time.monotonic()
        while not shutdown.is_set():
            if len(inflight) >= max_inflight:
                await asyncio.sleep(0.2)
                continue
            # 周期性回收崩溃 Worker 遗留消息 (不只依赖进程重启)
            if time.monotonic() - last_reclaim >= settings.WORKER_RECLAIM_INTERVAL_SECONDS:
                last_reclaim = time.monotonic()
                try:
                    dispatch(await queue.reclaim_stale(consumer))
                except Exception:
                    logger.exception("周期性回收 pending 任务失败")
            try:
                count = max(1, min(10, max_inflight - len(inflight)))
                messages = await queue.consume(consumer, count=count, block_ms=2000)
            except asyncio.CancelledError:
                raise
            except Exception:
                # 单次 Redis 抖动不应终止消费循环 (否则任务会一直排队)
                logger.exception("消费队列异常, 2s 后重试")
                await asyncio.sleep(2)
                continue
            dispatch(messages)

    loop_task = asyncio.create_task(consume_loop())
    await shutdown.wait()
    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass

    if inflight:
        logger.info("等待 %d 个在途任务完成 (最多 %ds)", len(inflight),
                    settings.WORKER_SHUTDOWN_TIMEOUT_SECONDS)
        _done, pending = await asyncio.wait(
            inflight, timeout=settings.WORKER_SHUTDOWN_TIMEOUT_SECONDS)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            logger.warning("%d 个在途任务未在超时内完成, 消息保留在 PEL 待回收", len(pending))
    await redis.aclose()


if __name__ == "__main__":
    def _stop(*_):
        shutdown.set()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            import asyncio as _a
            loop = asyncio.get_event_loop()
            loop.add_signal_handler(sig, _stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, _stop)  # Windows
    asyncio.run(main())
