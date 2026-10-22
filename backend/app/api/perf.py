"""性能分析 API.

- `POST /perf/upload`: 上传压缩包 (tar/zip/gz) 或单文件, 后台解压并自动搜寻本页签对应的
  原始数据 (sar / 抓包 / blktrace / 中断计数), 异步解析后写入报告, 前端订阅 SSE 看进度;
- 4 个 `*/analyze` 为早期同步接口 (单文件 <= 100MB, 不落盘不入队), 保留兼容但已废弃.
"""
import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import UploadResponse
from app.api.tasks import _save_upload
from app.core.audit import log_audit
from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user, get_redis
from app.models.task import Task, TaskStatus, TaskType
from app.models.user import User
from app.modules.perf import (ANALYZERS, PERF_KINDS, analyze_blktrace, analyze_interrupts,
                              analyze_netcap, analyze_sar)
from app.modules.perf.errors import PerfAnalysisError
from app.queue.task_queue import TaskQueue

router = APIRouter(prefix="/perf", tags=["perf"])
settings = get_settings()

MAX_ANALYZE_BYTES = 100 * 1024 * 1024  # 旧同步接口单文件上限 (内存解析)
MAX_UPLOAD_BYTES = 500 * 1024 * 1024   # 压缩包上传上限 500MB


async def _read_upload(file: UploadFile, limit: int = MAX_ANALYZE_BYTES) -> bytes:
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "文件超出分析上限 (100MB)")
    return data


async def _run(analyzer, file: UploadFile) -> dict:
    data = await _read_upload(file)
    try:
        return analyzer(data, file.filename or "")
    except PerfAnalysisError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.post("/upload", response_model=UploadResponse)
async def perf_upload(
    file: Annotated[UploadFile, File()],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    redis: Annotated[object, Depends(get_redis)],
    kind: Annotated[str, Form()] = "sar",           # sar / netcap / blktrace / interrupts
    task_name: Annotated[str, Form()] = "",
):
    """上传压缩包 (tar/zip/tar.gz) 或单文件, 后台解压并搜寻对应数据源.

    不设后缀白名单: blktrace 原始文件名为 `blktrace.0`、sar 二进制为 `sa15` 等均无标准后缀,
    仅按大小限制 + 内容嗅探判定。
    """
    if kind not in ANALYZERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"不支持的性能分析类型: {kind} (可选 {', '.join(PERF_KINDS)})")
    if not (file.filename or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "未选择上传文件")

    rate_key = f"rate:upload:{user.id}"
    count = await redis.incr(rate_key)
    if count == 1:
        await redis.expire(rate_key, 60)
    if count > settings.UPLOAD_RATE_LIMIT_PER_MIN:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "上传过于频繁, 请稍后再试")

    task_id = uuid.uuid4()
    upload_dir = Path(settings.DATA_DIR) / "uploads" / str(task_id)
    try:
        storage_path, size = await _save_upload(file, upload_dir, MAX_UPLOAD_BYTES)
    except Exception:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise

    task = Task(
        id=task_id, type=TaskType.perf, status=TaskStatus.pending,
        user_id=user.id, filename=storage_path.name,
        storage_path=str(storage_path), params={"perf_kind": kind},
        name=task_name, log_size=size,     # team_id 留空: 可见性为本人 + 平台管理员
    )
    db.add(task)
    log_audit(db, user, "创建性能分析任务", f"{kind} / {task_name or storage_path.name}")
    await db.commit()

    queue = TaskQueue(redis)
    await queue.enqueue(str(task_id), "perf",
                        {"storage_path": str(storage_path), "perf_kind": kind})
    return UploadResponse(task_id=task_id, status=task.status)


@router.post("/netcap/analyze", deprecated=True)
async def analyze_netcap_file(
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
):
    """网络抓包结果分析: 支持 .pcap 原始文件或 tshark 字段导出文本."""
    return await _run(analyze_netcap, file)


@router.post("/blktrace/analyze", deprecated=True)
async def analyze_blktrace_file(
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
):
    """blktrace 结果分析: 输入为 blkparse 文本输出."""
    return await _run(analyze_blktrace, file)


@router.post("/sar/analyze", deprecated=True)
async def analyze_sar_file(
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
):
    """sar 性能分析: 输入为 sar -u / -r -d (可选 -q) 的文本输出."""
    return await _run(analyze_sar, file)


@router.post("/interrupts/analyze", deprecated=True)
async def analyze_interrupts_file(
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
):
    """中断计数变化分析: 输入为 /proc/interrupts 多次快照 或 sar -I 文本输出."""
    return await _run(analyze_interrupts, file)
