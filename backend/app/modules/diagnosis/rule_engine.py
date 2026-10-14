"""规则引擎: 从数据库加载启用的规则 (版本化热加载), 对解析结果匹配打分.

规则分四类: regex/keyword 逐行匹配, threshold 指标阈值, script 子进程执行。
script 规则在独立子进程中运行 (见 docs/脚本规则设计.md), 失败/超时不使任务失败。
"""
import asyncio
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.rule import MatchType, Rule, Severity
from app.modules.diagnosis.parsers import LogEntry, ParseResult

logger = logging.getLogger(__name__)

SEVERITY_WEIGHT = {Severity.info: 1, Severity.warn: 3, Severity.error: 7, Severity.critical: 10}

# 脚本子进程入口绝对路径; 与 Worker 同一解释器, 以 -I 隔离模式拉起
SCRIPT_RUNNER = Path(__file__).with_name("script_runner.py").resolve()
VALID_SEVERITIES = {s.value for s in Severity}


@dataclass
class Finding:
    rule_id: int
    rule_name: str
    severity: str
    line_no: int
    raw: str
    matched_text: str = ""
    level: str = ""          # 命中行的日志级别 (regex/keyword 规则填充, 供巡检检查项归因)


@dataclass
class ScriptError:
    """单条脚本规则执行失败信息, 供任务事件流与报告展示."""
    rule_id: int
    rule_name: str
    error: str


@dataclass
class ScriptOutcome:
    """脚本规则批量执行汇总."""
    findings: list[Finding] = field(default_factory=list)
    errors: list[ScriptError] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    durations: list[dict] = field(default_factory=list)


@dataclass
class _RuleResult:
    """单条脚本规则执行结果."""
    findings: list[Finding]
    error: ScriptError | None
    logs: list[str]
    duration: dict


def _child_env() -> dict:
    """子进程环境: 丢弃 PYTHON* 变量 (与 -I 双重保险), 其余继承 (跨平台必需项)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}


def _fit_entries(entries: list[dict], settings) -> tuple[list[dict], bool]:
    """按条数 (SCRIPT_MAX_ENTRIES) 与字节 (SCRIPT_MAX_BYTES) 双上限截断上下文, 返回 (entries, truncated)."""
    truncated = False
    if len(entries) > settings.SCRIPT_MAX_ENTRIES:
        entries = entries[:settings.SCRIPT_MAX_ENTRIES]
        truncated = True
    n = len(entries)
    while n > 0:
        blob = json.dumps(entries[:n], ensure_ascii=False, default=str)
        if len(blob.encode("utf-8")) <= settings.SCRIPT_MAX_BYTES:
            break
        n //= 2
        truncated = True
    return entries[:n], truncated


def _runner_error(stdout: bytes, stderr: str) -> str:
    """子进程非零退出时提取错误: 优先 runner 结构化 error, 兜底 stderr."""
    try:
        data = json.loads(stdout.decode("utf-8"))
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
    except (ValueError, UnicodeDecodeError):
        pass
    return stderr[:300] or "脚本执行失败 (无错误输出)"


class RuleEngine:
    """规则引擎: 每任务实例化一次; 规则集从 DB 加载最新启用版本.

    热加载: 规则表 version 全局递增, Worker 每任务加载时取最新, 无需重启.
    """

    def __init__(self, rules: list[Rule]):
        self._rules = rules
        self._compiled: list[tuple[Rule, re.Pattern | None]] = []
        for rule in rules:
            pattern = None
            if rule.match_type == MatchType.regex:
                pattern = re.compile(rule.pattern)  # 保存前已校验
            self._compiled.append((rule, pattern))

    @classmethod
    async def load(cls, db: AsyncSession, team_id: int | None = None) -> "RuleEngine":
        return cls(await cls.load_rules(db, team_id))

    @staticmethod
    async def resolve_team_id(db: AsyncSession, task) -> int | None:
        """任务作用域团队: 直接取任务归属团队; 无团队任务返回 None(仅通用规则)."""
        return task.team_id

    @staticmethod
    async def load_rules(db: AsyncSession, team_id: int | None = None) -> list[Rule]:
        """加载启用规则: team_id=None 只取平台通用; 指定团队时取「通用 ∪ 本团队」.

        同名冲突时团队规则覆盖通用规则 (团队可定制而不影响其他团队)。
        """
        conds = [Rule.enabled.is_(True)]
        if team_id is None:
            conds.append(Rule.team_id.is_(None))
        else:
            conds.append(or_(Rule.team_id.is_(None), Rule.team_id == team_id))
        rules = list((await db.execute(select(Rule).where(*conds))).scalars().all())
        if team_id is not None:
            chosen: dict[str, Rule] = {}
            for r in rules:
                cur = chosen.get(r.name)
                if cur is None or (cur.team_id is None and r.team_id == team_id):
                    chosen[r.name] = r
            rules = list(chosen.values())
        return rules

    @classmethod
    async def load_for_task(cls, db: AsyncSession, task) -> "RuleEngine":
        """按任务作用域团队加载启用规则 (通用 ∪ 本团队)."""
        return cls(await cls.load_rules(db, await cls.resolve_team_id(db, task)))

    def script_rules(self) -> list[Rule]:
        """启用中的脚本规则, 按 priority, id 排序 (决定结果合并顺序)."""
        rules = [r for r in self._rules if r.match_type == MatchType.script]
        return sorted(rules, key=lambda r: (r.priority, r.id))

    def match_entries(self, entries: list[LogEntry]) -> list[Finding]:
        """对日志条目跑正则/关键字规则, 返回命中明细."""
        findings: list[Finding] = []
        for line_no, entry in enumerate(entries, 1):
            for rule, pattern in self._compiled:
                if rule.match_type == MatchType.regex:
                    m = pattern.search(entry.raw) if pattern else None
                    if m:
                        findings.append(Finding(rule.id, rule.name, rule.severity.value,
                                                line_no, entry.raw[:500], m.group(0)[:200], entry.level))
                elif rule.match_type == MatchType.keyword:
                    if rule.pattern and rule.pattern in entry.raw:
                        findings.append(Finding(rule.id, rule.name, rule.severity.value,
                                                line_no, entry.raw[:500], rule.pattern, entry.level))
        return findings

    def match_metrics(self, metrics: list[dict]) -> list[Finding]:
        """对指标点跑阈值规则."""
        findings: list[Finding] = []
        for rule, _ in self._compiled:
            if rule.match_type != MatchType.threshold:
                continue
            spec = rule.threshold or {}
            metric, op, value = spec.get("metric"), spec.get("op"), spec.get("value")
            if not metric or op not in (">", "<", ">=", "<=", "==") or value is None:
                continue
            for point in metrics:
                if point.get("metric_name") != metric:
                    continue
                if eval(f"{point['value']} {op} {value}"):  # noqa: 值来自受控配置, 保存时已校验
                    findings.append(Finding(
                        rule.id, rule.name, rule.severity.value, 0,
                        f"{point.get('host', '')} {metric}={point['value']} ({op} {value})"))
        return findings

    async def run_scripts(self, ctx: dict) -> ScriptOutcome:
        """对所有脚本规则并发执行 (信号量限并发); 单规则失败不影响其他规则与任务.

        ctx 由 pipeline 构建: {task, entries, metrics, file_stats}; 每条规则执行时
        再补上 rule 块 (含 params)。上下文超出上限时截断并置 meta.truncated。
        """
        outcome = ScriptOutcome()
        rules = self.script_rules()
        if not rules:
            return outcome
        settings = get_settings()
        root = Path(settings.RULE_SCRIPT_DIR).resolve()
        entries, truncated = _fit_entries(ctx.get("entries") or [], settings)
        metrics = ctx.get("metrics") or []
        base = {
            "task": ctx.get("task") or {},
            "metrics": metrics,
            "file_stats": ctx.get("file_stats") or {},
            "meta": {
                "total_entries": len(ctx.get("entries") or []),
                "total_metrics": len(metrics),
                "truncated": truncated,
            },
        }
        sem = asyncio.Semaphore(max(1, settings.RULE_SCRIPT_CONCURRENCY))
        results = await asyncio.gather(
            *[self._run_one(r, root, entries, base, settings, sem) for r in rules])
        for res in results:
            outcome.findings.extend(res.findings)
            outcome.logs.extend(res.logs)
            outcome.durations.append(res.duration)
            if res.error is not None:
                outcome.errors.append(res.error)
        return outcome

    async def _run_one(self, rule: Rule, root: Path, entries: list[dict], base: dict,
                       settings, sem: asyncio.Semaphore) -> _RuleResult:
        """执行单条脚本规则: 独立子进程 + per-rule 超时, 超时即 kill."""
        ctx = {
            "rule": {"id": rule.id, "name": rule.name,
                     "severity": rule.severity.value, "params": rule.script_params or {}},
            "entries": entries,
            **base,
        }
        payload = json.dumps({"script_root": str(root), "script_path": rule.script_path,
                              "ctx": ctx}, ensure_ascii=False, default=str).encode("utf-8")
        timeout = rule.script_timeout or settings.RULE_SCRIPT_TIMEOUT
        started = time.monotonic()
        async with sem:
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-I", str(SCRIPT_RUNNER),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, cwd=str(root), env=_child_env())
                try:
                    out, err = await asyncio.wait_for(proc.communicate(payload), timeout=timeout)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    return self._failed(rule, f"执行超时 (>{timeout}s)", started)
            except OSError as exc:
                return self._failed(rule, f"子进程启动失败: {exc}", started)
        duration = {"rule_id": rule.id, "rule_name": rule.name,
                    "seconds": round(time.monotonic() - started, 3)}
        stderr_text = err.decode("utf-8", "ignore")
        if proc.returncode != 0:
            return _RuleResult([], ScriptError(rule.id, rule.name,
                               _runner_error(out, stderr_text)), [], duration)
        try:
            data = json.loads(out.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return _RuleResult([], ScriptError(rule.id, rule.name,
                               f"stdout 非法 JSON: {stderr_text[:200] or out[:100]!r}"), [], duration)
        if not data.get("ok"):
            return _RuleResult([], ScriptError(rule.id, rule.name,
                               str(data.get("error") or "脚本执行失败")), [], duration)
        result = data.get("result") or {}
        findings, bad_logs = self._normalize_findings(rule, result.get("findings"), entries)
        logs = [f"[{rule.name}] {x}" for x in (list(result.get("logs") or []) + bad_logs)[:20]]
        return _RuleResult(findings, None, logs, duration)

    def _failed(self, rule: Rule, reason: str, started: float) -> _RuleResult:
        duration = {"rule_id": rule.id, "rule_name": rule.name,
                    "seconds": round(time.monotonic() - started, 3)}
        return _RuleResult([], ScriptError(rule.id, rule.name, reason), [], duration)

    def _normalize_findings(self, rule: Rule, raw, entries: list[dict]
                            ) -> tuple[list[Finding], list[str]]:
        """校验并规范化脚本输出 findings: 非法字段降级为规则默认值, 超上限截断."""
        if raw is None:
            return [], []
        if not isinstance(raw, list):
            return [], ["findings 字段应为数组, 已忽略"]
        findings: list[Finding] = []
        logs: list[str] = []
        limit = get_settings().SCRIPT_MAX_FINDINGS
        for idx, item in enumerate(raw):
            if len(findings) >= limit:
                logs.append(f"findings 超过上限 {limit}, 已截断")
                break
            if not isinstance(item, dict):
                logs.append(f"第 {idx + 1} 条 finding 非对象, 已忽略")
                continue
            try:
                line_no = int(item.get("line_no") or 0)
            except (TypeError, ValueError):
                line_no = 0
            severity = item.get("severity")
            if severity not in VALID_SEVERITIES:
                severity = rule.severity.value
            fallback = entries[line_no - 1]["raw"] if 0 < line_no <= len(entries) else ""
            text = str(item.get("message") or item.get("raw") or fallback or "").strip()
            findings.append(Finding(
                rule.id, rule.name, severity, line_no,
                text[:500] or f"脚本规则「{rule.name}」命中",
                str(item.get("matched_text") or "")[:200]))
        return findings, logs

    @staticmethod
    def health_score(findings: list[Finding]) -> int:
        """健康评分: 100 - 加权扣分, 下限 0."""
        penalty = sum(SEVERITY_WEIGHT[Severity(f.severity)] for f in findings)
        return max(0, 100 - min(100, penalty))