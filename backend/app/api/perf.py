"""性能分析 API: 上传抓包/blktrace/sar/中断计数文件后同步解析并返回聚合结果.

- 无外部依赖 (纯 Python 解析), 单文件上限 100MB, 解析在请求内完成, 不落盘不入队
- 解析失败以 400 返回可读原因, 便于前端直接提示
"""
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.deps import get_current_user
from app.models.user import User
from app.modules.perf import analyze_blktrace, analyze_interrupts, analyze_netcap, analyze_sar
from app.modules.perf.errors import PerfAnalysisError

router = APIRouter(prefix="/perf", tags=["perf"])

MAX_ANALYZE_BYTES = 100 * 1024 * 1024  # 单文件上限 100MB (内存解析)


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


@router.post("/netcap/analyze")
async def analyze_netcap_file(
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
):
    """网络抓包结果分析: 支持 .pcap 原始文件或 tshark 字段导出文本."""
    return await _run(analyze_netcap, file)


@router.post("/blktrace/analyze")
async def analyze_blktrace_file(
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
):
    """blktrace 结果分析: 输入为 blkparse 文本输出."""
    return await _run(analyze_blktrace, file)


@router.post("/sar/analyze")
async def analyze_sar_file(
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
):
    """sar 性能分析: 输入为 sar -u / -r / -d (可选 -q) 的文本输出."""
    return await _run(analyze_sar, file)


@router.post("/interrupts/analyze")
async def analyze_interrupts_file(
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
):
    """中断计数变化分析: 输入为 /proc/interrupts 多次快照 或 sar -I 文本输出."""
    return await _run(analyze_interrupts, file)