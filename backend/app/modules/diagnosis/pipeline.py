"""诊断流水线: 解压校验 → 解析 → 规则引擎 → LLM 总结 → 报告, 全程事件流输出."""
import logging
import shutil
import tarfile
import zipfile
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.events.event_bus import EventBus
from app.models.metric import MetricPoint
from app.models.rule import Rule
from app.models.task import Report, Task, TaskStatus
from app.modules.diagnosis import llm
from app.modules.diagnosis.parsers import identify_and_parse
from app.modules.diagnosis.rule_engine import Finding, RuleEngine

logger = logging.getLogger(__name__)

MAX_ENTRIES_FOR_CONTEXT = 200
EXTRACT_LIMIT_FILES = 5000
EXTRACT_LIMIT_BYTES = 2 * 1024 * 1024 * 1024  # 2GB 防 zip 炸弹


def safe_extract(archive_path: Path, dest: Path) -> list[Path]:
    """解压日志包 (zip/tar.gz), 防路径穿越与超大成员.

    - 目录 (多日志任务): 收集目录内文件, 遇压缩包就地解压到 dest
    - 压缩包: 解压其成员
    - 普通文件: 直接返回分析
    """
    dest.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    if archive_path.is_dir():
        dest_resolved = str(dest.resolve())
        for f in sorted(archive_path.rglob("*")):
            if len(extracted) >= EXTRACT_LIMIT_FILES:
                break
            if not f.is_file() or f.is_symlink():
                continue
            if str(f.resolve()).startswith(dest_resolved):
                continue  # 跳过解压临时目录自身, 防递归
            if zipfile.is_zipfile(f) or tarfile.is_tarfile(f):
                extracted.extend(safe_extract(f, dest / f.stem))
            else:
                extracted.append(f)
        return extracted
    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as zf:
            total = 0
            for info in zf.infolist():
                if len(extracted) >= EXTRACT_LIMIT_FILES or total > EXTRACT_LIMIT_BYTES:
                    break
                target = (dest / info.filename).resolve()
                if not str(target).startswith(str(dest.resolve())):  # 路径穿越防护
                    continue
                total += info.file_size
                if not info.is_file():
                    continue
                zf.extract(info, dest)
                extracted.append(target)
    elif tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path) as tf:
            for member in tf.getmembers()[:EXTRACT_LIMIT_FILES]:
                if not member.isfile() or member.size > 256 * 1024 * 1024:
                    continue
                target = (dest / member.name).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    continue
                tf.extract(member, dest)
                extracted.append(target)
    else:
        extracted.append(archive_path)  # 单文件直接分析
    return extracted


async def run_diagnosis(task_id: UUID, bus: EventBus, is_cancelled) -> None:
    """执行诊断任务全流程, 每步通过 bus.publish 实时输出."""
    async with AsyncSessionLocal() as db:
        task = (await db.execute(db_get(Task, task_id))).scalar_one_or_none()
        if task is None:
            return
        task.status = TaskStatus.running
        await db.commit()

        try:
            await _pipeline(db, task, bus, is_cancelled)
            task.status = TaskStatus.done
            task.progress = 100
        except _Cancelled:
            task.status = TaskStatus.cancelled
        except Exception as e:
            logger.exception("诊断任务失败 %s", task_id)
            task.status = TaskStatus.failed
            task.error = str(e)[:2000]
            await bus.publish(str(task_id), "error", {"message": task.error})
        await db.commit()
        await bus.publish(str(task_id), "done", {"status": task.status})


def db_get(model, pk):
    from sqlalchemy import select
    return select(model).where(model.id == pk)


class _Cancelled(Exception):
    pass


async def _pipeline(db: AsyncSession, task: Task, bus: EventBus, is_cancelled) -> None:
    task_id = str(task.id)

    # 1. 解压 (storage_path 可能是单文件或多文件的目录)
    await bus.publish(task_id, "progress", {"step": "extract", "percent": 5, "message": "解压与安全校验"})
    base = Path(task.storage_path)
    work_dir = (base if base.is_dir() else base.parent) / "extracted"
    files = safe_extract(base, work_dir)
    if not files:
        raise RuntimeError("日志包为空或格式不支持")
    if await is_cancelled():
        raise _Cancelled()

    # 2. 解析 (插件识别) + 3. 规则匹配: regex/keyword 逐文件
    engine = await RuleEngine.load_for_task(db, task)  # 按任务作用域团队加载(通用∪本团队), 天然热加载
    all_findings: list[Finding] = []
    all_metrics: list[dict] = []
    file_stats: dict = {}
    script_entries: list[dict] = []  # 供脚本规则: 全局递增 line_no + 来源文件
    global_line = 0
    for i, path in enumerate(files):
        if await is_cancelled():
            raise _Cancelled()
        await bus.publish(task_id, "progress", {
            "step": "parse", "percent": 10 + int(40 * (i + 1) / len(files)),
            "message": f"解析 {path.name} ({i + 1}/{len(files)})"})
        result = await identify_and_parse(path, task.params.get("server_type", ""))
        if result is None:
            continue
        file_stats[str(path.name)] = result.file_stats
        all_metrics.extend(result.metrics)
        all_findings.extend(engine.match_entries(result.entries))
        for entry in result.entries:
            global_line += 1
            script_entries.append({
                "line_no": global_line,
                "raw": entry.raw[:1000],
                "ts": entry.timestamp.isoformat() if entry.timestamp else None,
                "level": entry.level,
                "component": entry.component,
                "file": path.name,
                "fields": entry.fields,
            })

    # 3b. 阈值规则 (指标)
    all_findings.extend(engine.match_metrics(all_metrics))

    # 3c. 脚本规则 (独立子进程执行; 失败/超时不使任务失败)
    await bus.publish(task_id, "progress", {"step": "rules", "percent": 60,
                                            "message": f"规则匹配完成, 命中 {len(all_findings)} 条"})
    script_outcome = await engine.run_scripts({
        "task": {"id": task_id, "task_type": task.type.value,
                 "server_type": task.params.get("server_type", "")},
        "entries": script_entries,
        "metrics": all_metrics,
        "file_stats": file_stats,
    })
    all_findings.extend(script_outcome.findings)
    for message in script_outcome.logs:
        await bus.publish(task_id, "log", {"message": message})
    for err in script_outcome.errors:
        logger.error("脚本规则执行失败 rule_id=%s name=%s: %s", err.rule_id, err.rule_name, err.error)
        await bus.publish(task_id, "warning", {
            "message": f"脚本规则「{err.rule_name}」执行失败: {err.error}"})

    for f in all_findings[:100]:
        await bus.publish(task_id, "finding", {
            "rule_name": f.rule_name, "severity": f.severity,
            "line_no": f.line_no, "text": f.raw[:300], "matched": f.matched_text})

    # 4. 指标入库
    if all_metrics:
        await bus.publish(task_id, "progress", {"step": "metrics", "percent": 70, "message": "性能指标入库"})
        db.add_all([MetricPoint(**m) for m in all_metrics[:100000]])

    # 5. LLM 总结 (流式 token 逐个推送)
    score = RuleEngine.health_score(all_findings)
    finding_dicts = [vars(f) for f in all_findings[:MAX_ENTRIES_FOR_CONTEXT]]
    await bus.publish(task_id, "progress", {"step": "llm", "percent": 80, "message": "智能总结生成中"})
    summary_parts: list[str] = []
    context = "规则命中结果:\n" + "\n".join(
        f"[{f['severity']}] {f['rule_name']}: {f.get('matched_text') or f.get('raw', '')[:150]}"
        for f in finding_dicts) if finding_dicts else "未命中任何规则"
    async for token in llm.stream_summary(context):
        if await is_cancelled():
            raise _Cancelled()
        summary_parts.append(token)
        await bus.publish(task_id, "llm_token", {"text": token})
    summary = "".join(summary_parts) if llm.llm_enabled() else llm.rule_only_summary(finding_dicts)

    # 6. 报告
    await bus.publish(task_id, "progress", {"step": "report", "percent": 95, "message": "生成报告"})
    report = Report(
        task_id=task.id, health_score=score, summary=summary,
        findings=finding_dicts,
        content={"file_stats": file_stats, "metric_count": len(all_metrics),
                 "script_runs": script_outcome.durations,
                 "script_errors": [vars(e) for e in script_outcome.errors]},
    )
    db.add(report)
    task.progress = 95

    # 清理解压临时目录
    shutil.rmtree(work_dir, ignore_errors=True)
