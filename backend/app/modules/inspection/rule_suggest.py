"""智能巡检规则推荐: 评估现有规则对任务日志的覆盖完善程度, 推荐可新增的规则项.

- 事件通道独立: {task_id}:suggest:{run_id}
- 并发去重: Redis 锁 suggest:lock:{task_id}
- LLM 已配置: llm.stream_rule_suggestions 逐 token 推送 suggestion_token, 末段 ```json 解析为建议项
- LLM 未配置: 启发式降级 —— 重解析日志, 比对现有规则已覆盖行, 对未覆盖的错误/严重日志
  按关键词模式表给出建议项, 评估文本按打字机节奏逐行推送
- 完成后 upsert rule_suggestions (与任务一对一), 不写入报告
"""
import asyncio
import json
import logging
import re
import shutil
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.events.event_bus import EventBus
from app.models.rule import Rule
from app.models.rule_suggestion import RuleSuggestion
from app.models.task import Task
from app.modules.diagnosis import llm
from app.modules.diagnosis.pipeline import safe_extract
from app.modules.diagnosis.parsers import LogEntry, identify_and_parse
from app.modules.diagnosis.rule_engine import VALID_SEVERITIES, RuleEngine

logger = logging.getLogger(__name__)

LOCK_TTL_SECONDS = 600        # 生成锁 TTL (防进程崩溃后死锁)
MAX_ENTRIES = 100000          # 参与匹配的事件上限
TYPEWRITER_INTERVAL = 0.11    # 启发式评估打字机节奏 (秒/行)
MAX_SAMPLES = 30              # 送入 LLM 的未覆盖样例上限

# 未覆盖错误模式 → 建议关键字规则: (建议规则名, 关键字, 严重级别, 说明)
_UNCOVERED_PATTERNS: list[tuple[str, str, str, str]] = [
    ("内存不足", "out of memory", "critical", "检测到内存分配失败 / OOM"),
    ("OOM 杀进程", "killed process", "critical", "检测到 OOM Killer 终止进程"),
    ("磁盘 IO 错误", "i/o error", "error", "检测到磁盘 I/O 读写出错"),
    ("磁盘空间耗尽", "no space left on device", "critical", "检测到磁盘空间不足"),
    ("连接被拒绝", "connection refused", "error", "检测到服务连接被拒绝"),
    ("请求超时", "timeout", "warn", "检测到请求 / 操作超时"),
    ("权限不足", "permission denied", "error", "检测到权限校验失败"),
    ("段错误", "segmentation fault", "critical", "检测到进程崩溃 (段错误)"),
    ("服务不可用", "service unavailable", "error", "检测到依赖服务不可用"),
    ("文件缺失", "no such file or directory", "warn", "检测到引用的文件缺失"),
]

_JSON_BLOCK = re.compile(r"```json\s*(.*?)```", re.S | re.I)


class _Cancelled(Exception):
    pass


async def run_rule_suggest(task_id: UUID, run_id: str, bus: EventBus, is_cancelled) -> None:
    """对已完成的巡检任务按需生成规则推荐, 事件流输出到 {task_id}:suggest:{run_id}."""
    key = f"{task_id}:suggest:{run_id}"
    lock_key = f"suggest:lock:{task_id}"
    redis = bus.redis

    got_lock = await redis.set(lock_key, run_id, nx=True, ex=LOCK_TTL_SECONDS)
    if not got_lock:
        await bus.publish(key, "error", {"message": "该任务已有推荐生成中, 请稍候"})
        await bus.publish(key, "done", {"status": "skipped"})
        return

    try:
        await _generate(task_id, run_id, key, bus, is_cancelled)
        await bus.publish(key, "done", {"status": "done"})
    except _Cancelled:
        await bus.publish(key, "done", {"status": "cancelled"})
    except Exception as e:
        logger.exception("规则推荐生成失败 %s", task_id)
        await bus.publish(key, "error", {"message": str(e)[:2000]})
        await bus.publish(key, "done", {"status": "failed"})
    finally:
        await redis.delete(lock_key)


async def _generate(task_id: UUID, run_id: str, key: str, bus: EventBus, is_cancelled) -> None:
    async with AsyncSessionLocal() as db:
        task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if task is None:
            raise RuntimeError("任务不存在")
        if await is_cancelled():
            raise _Cancelled()

        # 1. 重解析任务日志 (巡检任务的条目未持久化, 需重新解析)
        await bus.publish(key, "progress", {"percent": 8, "message": "解析巡检日志"})
        entries = await _parse_entries(task)
        if not entries:
            raise RuntimeError("任务无可用日志条目, 无法评估规则完善度")

        # 2. 现有规则覆盖情况
        rules = await RuleEngine.load_rules(db, await RuleEngine.resolve_team_id(db, task))
        engine = RuleEngine(list(rules))
        findings = engine.match_entries(entries[:MAX_ENTRIES])
        covered_lines = {f.line_no for f in findings}
        uncovered = [(i, e) for i, e in enumerate(entries[:MAX_ENTRIES], 1)
                     if e.level in ("error", "critical") and i not in covered_lines]

        # 3. 启发式建议 (作为 LLM 失败/未配置时的兜底)
        heuristic_items = _heuristic_items(uncovered)

        model_name = "heuristic"
        if llm.llm_enabled():
            await bus.publish(key, "progress", {"percent": 30, "message": "LLM 评估规则完善度"})
            context = _build_context(entries, rules, findings, uncovered)
            parts: list[str] = []
            async for token in llm.stream_rule_suggestions(context):
                if await is_cancelled():
                    raise _Cancelled()
                parts.append(token)
                await bus.publish(key, "suggestion_token", {"text": token})
            model_name = get_settings().LLM_MODEL
            raw_text = "".join(parts)
            assessment = _strip_json_block(raw_text)
            items = _extract_items(raw_text) or heuristic_items
        else:
            assessment = _heuristic_assessment(entries, rules, findings, uncovered, heuristic_items)
            items = heuristic_items
            for line in assessment.splitlines(keepends=True):
                if await is_cancelled():
                    raise _Cancelled()
                await bus.publish(key, "suggestion_token", {"text": line, "line": True})
                await asyncio.sleep(TYPEWRITER_INTERVAL)

        # 4. 推送结构化建议项 + 清理后的评估文本
        await bus.publish(key, "suggestion", {"assessment": assessment, "items": items})

        # 5. upsert 持久化 (刷新/切页签可复看; 不写入报告)
        existing = (await db.execute(
            select(RuleSuggestion).where(RuleSuggestion.task_id == task_id))).scalar_one_or_none()
        if existing is not None:
            existing.assessment = assessment
            existing.items = items
            existing.model = model_name
        else:
            db.add(RuleSuggestion(task_id=task_id, assessment=assessment,
                                  items=items, model=model_name))
        await db.commit()


async def _parse_entries(task: Task) -> list[LogEntry]:
    """重解析任务上传的日志包, 返回解析条目."""
    src = Path(task.storage_path)
    if not src.exists():
        return []
    work_dir = src.parent / "suggest_extracted"
    try:
        files = await asyncio.to_thread(safe_extract, src, work_dir)
        server_type = (task.params or {}).get("server_type", "")
        entries: list[LogEntry] = []
        for path in files:
            result = await asyncio.to_thread(identify_and_parse, path, server_type)
            if result:
                entries.extend(result.entries)
        return entries
    finally:
        await asyncio.to_thread(shutil.rmtree, work_dir, True)


def _heuristic_items(uncovered: list[tuple[int, LogEntry]]) -> list[dict]:
    """对未覆盖的错误/严重日志按关键词模式表生成建议关键字规则."""
    grouped: dict[str, dict] = {}
    for _line_no, entry in uncovered:
        text = entry.raw.lower()
        for name, keyword, severity, desc in _UNCOVERED_PATTERNS:
            if keyword in text:
                item = grouped.setdefault(keyword, {
                    "name": name, "description": desc, "match_type": "keyword",
                    "pattern": keyword, "severity": severity,
                    "reason": "", "hit_count": 0, "sample": entry.raw[:200],
                })
                item["hit_count"] += 1
                break
    items = list(grouped.values())
    for item in items:
        item["reason"] = f"日志中出现 {item['hit_count']} 次该模式, 当前规则未覆盖"
    items.sort(key=lambda x: x["hit_count"], reverse=True)
    return items


def _coverage_line(total: int, findings: list, uncovered: list) -> str:
    covered = len({f.line_no for f in findings})
    pct = round(100 * covered / total, 1) if total else 0.0
    return (f"- 日志事件总数: {total}\n"
            f"- 已命中规则的行: {covered} 行 (行覆盖率 {pct}%)\n"
            f"- 错误/严重日志未覆盖: {len(uncovered)} 行")


def _heuristic_assessment(entries: list[LogEntry], rules: list[Rule], findings: list,
                          uncovered: list, items: list) -> str:
    lines = [
        "规则完善度评估 (启发式, 未配置 LLM):",
        _coverage_line(len(entries), findings, uncovered),
        f"- 现有启用规则: {len(rules)} 条",
    ]
    if items:
        lines.append(f"- 建议新增规则项: {len(items)} 条 (详见下方建议列表)")
        lines.append("建议新增的规则项已按命中频次排序, 可编辑后一键接纳。")
    else:
        lines.append("- 未发现明显的规则覆盖缺口, 当前规则对该日志覆盖较好。")
    return "\n".join(lines)


def _build_context(entries: list[LogEntry], rules: list[Rule], findings: list,
                   uncovered: list) -> str:
    parts = [
        "现有启用规则:",
        "\n".join(
            f"- [{r.severity.value}] {r.name} | {r.match_type.value} | pattern={r.pattern[:120]}"
            f" | {r.description[:80]}" for r in rules) or "(无)",
        "",
        _coverage_line(len(entries), findings, uncovered),
        "",
        f"未覆盖的错误/严重日志样例 (最多 {MAX_SAMPLES} 条):",
        "\n".join(f"[{e.level}] {e.raw[:200]}" for _n, e in uncovered[:MAX_SAMPLES]) or "(无)",
    ]
    return "\n".join(parts)


def _strip_json_block(text: str) -> str:
    return _JSON_BLOCK.sub("", text).strip()


def _extract_items(text: str) -> list[dict]:
    """从 LLM 输出中解析 ```json 代码块为结构化建议项 (非法则返回空列表)."""
    m = _JSON_BLOCK.search(text)
    if not m:
        return []
    try:
        data = json.loads(m.group(1).strip())
    except ValueError:
        return []
    if isinstance(data, dict):
        data = data.get("items") or []
    if not isinstance(data, list):
        return []
    items: list[dict] = []
    for raw in data[:20]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        pattern = str(raw.get("pattern") or "").strip()
        if not name or not pattern:
            continue
        match_type = str(raw.get("match_type") or "keyword").strip().lower()
        if match_type not in ("keyword", "regex"):
            match_type = "keyword"
        severity = str(raw.get("severity") or "warn").strip().lower()
        if severity not in VALID_SEVERITIES:
            severity = "warn"
        items.append({
            "name": name[:128], "description": str(raw.get("description") or "")[:512],
            "match_type": match_type, "pattern": pattern[:512], "severity": severity,
            "reason": str(raw.get("reason") or "")[:512],
        })
    return items