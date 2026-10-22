"""性能分析流水线: 解压压缩包 → 搜寻原始数据 → 解析 → 报告持久化, 全程事件流输出.

与诊断/解析流水线一致的三态收尾: done / cancelled / failed;
识别不到数据源属业务结果而非故障, 任务仍为 done, 由报告 content.status="not_found" 表达。
"""
import asyncio
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.events.event_bus import EventBus
from app.models.task import Report, Task, TaskStatus
from app.modules.perf import ANALYZERS
from app.modules.perf.detect import (MAX_GROUPS_PER_KIND, PERF_MAX_FILE_BYTES, DetectResult,
                                     detect_kind, extract_for_perf)
from app.modules.perf.errors import PerfAnalysisError

logger = logging.getLogger(__name__)

PARSE_CONCURRENCY = 2   # CPU 密集解析并发上限 (避免 WORKER_CONCURRENCY 全被 perf 任务占用)


class _Cancelled(Exception):
    """任务被取消时抛出, 由 run_perf 收尾为 cancelled."""


_SEMAPHORE: asyncio.Semaphore | None = None


def _semaphore() -> asyncio.Semaphore:
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(PARSE_CONCURRENCY)
    return _SEMAPHORE


async def run_perf(task_id: UUID, bus: EventBus, is_cancelled) -> None:
    """执行性能分析任务全流程 (签名与 run_diagnosis 一致)."""
    async with AsyncSessionLocal() as db:
        task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if task is None:
            return
        # 条件更新: 已被取消/结束的任务不再执行; interrupted 允许崩溃回收后重跑
        result = await db.execute(
            update(Task)
            .where(Task.id == task_id,
                   Task.status.in_((TaskStatus.pending, TaskStatus.running, TaskStatus.interrupted)))
            .values(status=TaskStatus.running, started_at=datetime.now(timezone.utc))
        )
        await db.commit()
        if not result.rowcount:
            return
        await db.refresh(task)

        try:
            await _pipeline(db, task, bus, is_cancelled)
            task.status = TaskStatus.done
            task.progress = 100
        except _Cancelled:
            task.status = TaskStatus.cancelled
        except Exception as e:  # noqa: BLE001 流水线异常统一置 failed
            logger.exception("性能分析任务失败 %s", task_id)
            task.status = TaskStatus.failed
            task.error = str(e)[:2000]
            await bus.publish(str(task_id), "error", {"message": task.error})
        await db.commit()
        await bus.publish(str(task_id), "done", {"status": task.status})


async def _pipeline(db: AsyncSession, task: Task, bus: EventBus, is_cancelled) -> None:
    task_id = str(task.id)
    kind = str((task.params or {}).get("perf_kind") or "sar")
    if kind not in ANALYZERS:
        raise RuntimeError(f"未知的性能分析类型: {kind}")

    base = Path(task.storage_path)
    if not base.exists():
        raise RuntimeError(f"上传文件不存在: {task.filename or task.storage_path}")
    work_dir = (base if base.is_dir() else base.parent) / "extracted"
    try:
        await bus.publish(task_id, "progress",
                          {"step": "extract", "percent": 3, "message": "解压与安全校验"})
        # 解压为阻塞 IO (可能解出上千文件), 放线程池避免堵住事件循环
        files, warnings = await asyncio.to_thread(extract_for_perf, base, work_dir)
        if await is_cancelled():
            raise _Cancelled()
        if not files:
            raise RuntimeError("压缩包为空或格式不支持, 包内未找到任何文件")

        await bus.publish(task_id, "progress", {
            "step": "detect", "percent": 20,
            "message": f"嗅探 {len(files)} 个文件, 查找 {kind} 数据源"})
        found = await asyncio.to_thread(detect_kind, files, kind, MAX_GROUPS_PER_KIND, work_dir)
        warnings.extend(found.warnings)

        content: dict = {
            "kind": kind,
            "detected_kind": found.kind,
            "status": "not_found",
            "source": {"filename": task.filename, "entryCount": len(files),
                       "archiveBytes": task.log_size},
            "detected": _detected_list(found),
            "unsupported": found.unsupported,
            "inventory": found.inventory,
            "groups": [],
            "summary_text": "",
            "warnings": warnings,
            "parseMs": 0,
        }

        candidates = found.candidates if found.kind == kind else []
        groups: list[dict] = []
        parse_ms = 0
        total = len(candidates)
        for i, cand in enumerate(candidates):
            if await is_cancelled():
                raise _Cancelled()
            await bus.publish(task_id, "progress", {
                "step": "parse", "percent": 35 + int(50 * i / max(1, total)),
                "message": f"解析 {cand.rel} ({i + 1}/{total})"})
            data = await asyncio.to_thread(_read_bytes, cand.path, PERF_MAX_FILE_BYTES)
            if data is None:
                warnings.append(f"{cand.rel} 超过 {PERF_MAX_FILE_BYTES // 1024 // 1024}MB 或不可读, 已跳过")
                continue
            started = time.monotonic()
            try:
                async with _semaphore():
                    result = await asyncio.to_thread(ANALYZERS[kind], data, cand.rel)
            except PerfAnalysisError as e:
                logger.info("性能候选解析失败 %s: %s", cand.rel, e)
                warnings.append(f"{cand.rel} 解析失败: {e}")
                continue
            except Exception as e:  # noqa: BLE001 单个候选异常不影响其余候选
                logger.exception("性能候选解析异常 %s", cand.rel)
                warnings.append(f"{cand.rel} 解析异常: {e}")
                continue
            parse_ms += int((time.monotonic() - started) * 1000)
            groups.append({"id": f"g{len(groups) + 1}",
                           "label": _label(kind, result, cand.rel),
                           "file": cand.rel, "size": cand.size, "result": result})

        content["groups"] = groups
        content["parseMs"] = parse_ms
        content["warnings"] = warnings
        if groups:
            content["status"] = "ok" if len(groups) == total else "partial"
            content["summary_text"] = f"识别到 {len(groups)} 个 {kind} 数据源并完成解析"
        elif candidates:
            content["status"] = "not_found"
            content["summary_text"] = f"识别到 {len(candidates)} 个 {kind} 候选, 但全部解析失败 (详见提示)"
        else:
            content["status"] = "not_found"
            content["summary_text"] = _not_found_text(kind, found)
        if content["status"] == "not_found":
            await bus.publish(task_id, "warning", {"message": content["summary_text"]})

        await bus.publish(task_id, "progress",
                          {"step": "report", "percent": 95, "message": "生成分析结果"})
        # reports.task_id 有唯一约束, 任务被回收重跑时先删后插保证幂等
        await db.execute(delete(Report).where(Report.task_id == task.id))
        db.add(Report(task_id=task.id, summary=content["summary_text"],
                      findings=[], content=content))
        task.progress = 95
        await db.commit()
    finally:
        # 解压产物只服务于本次解析, 收尾清理 (取消/失败同样清理)
        await asyncio.to_thread(shutil.rmtree, work_dir, True)


def _read_bytes(path: Path, limit: int) -> bytes | None:
    """读取文件内容; 超过上限或不可读返回 None."""
    try:
        if path.stat().st_size > limit:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _label(kind: str, result: dict, rel: str) -> str:
    """分组标签: sar 带主机与日期, 其余用包内相对路径."""
    if kind == "sar" and result.get("host"):
        return f"{result['host']} · {result.get('date') or rel}".strip()
    return rel


def _not_found_text(kind: str, found: DetectResult) -> str:
    if found.kind and found.kind != kind:
        return f"包内未识别到 {kind} 数据源, 但检测到 {found.kind} 数据, 请切换到对应页签"
    return f"包内未识别到 {kind} 数据源"


def _detected_list(found: DetectResult) -> list[dict]:
    """按得分降序列出包内各类数据源的识别证据 (供前端「未识别」页面展示)."""
    kinds = sorted(found.scores, key=lambda k: -found.scores[k])
    return [{"kind": k, "score": found.scores[k],
             "evidence": found.evidence.get(k, []),
             "files": found.files_of.get(k, []),
             "groupCount": len(found.files_of.get(k, []))} for k in kinds]
