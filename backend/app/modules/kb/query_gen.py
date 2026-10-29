"""诊断检索 query 生成: 规则抽取 + (可选) LLM 改写."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 常见故障词 / 错误模式
_ERROR_WORDS = (
    r"error|fail(?:ed|ure)?|exception|timeout|timed\s*out|refused|unreachable|"
    r"oom|killed|panic|segfault|denied|forbidden|unauthorized|not\s*found|"
    r"crash|abort|hang|deadlock|corrupt|i/?o\s*error|no\s*space|"
    r"连接失败|超时|拒绝|不可用|异常|报错|崩溃|磁盘满|内存不足"
)
_CODE_RE = re.compile(r"\b(?:E[A-Z0-9_]{2,}|0x[0-9a-fA-F]{4,}|HTTP\s*[45]\d{2}|[45]\d{2})\b")
_PATH_RE = re.compile(r"(?:/[A-Za-z0-9._\-]+){2,}")
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_WORD_RE = re.compile(r"[\w\u4e00-\u9fff\-]{3,}")
_ERR_RE = re.compile(_ERROR_WORDS, re.I)


@dataclass
class QueryGenResult:
    queries: list[str]
    source: str = "rule"  # rule | llm | rule+llm
    note: str = ""


def rule_extract(text: str, *, max_n: int = 8, extras: list[str] | None = None) -> list[str]:
    """从用户问题与附加文本中抽取检索 query（去重保序）."""
    blob = (text or "").strip()
    if extras:
        blob = blob + "\n" + "\n".join(x for x in extras if x)
    if not blob:
        return []

    found: list[str] = []

    def add(q: str) -> None:
        q = re.sub(r"\s+", " ", (q or "").strip())
        if len(q) < 2 or len(q) > 120:
            return
        low = q.lower()
        if any(low == x.lower() for x in found):
            return
        found.append(q)

    for m in _CODE_RE.finditer(blob):
        add(m.group(0))
    for m in _IP_RE.finditer(blob):
        add(m.group(0))
    for m in _PATH_RE.finditer(blob):
        p = m.group(0)
        if len(p) <= 80:
            add(p)

    for m in _ERR_RE.finditer(blob):
        start = max(0, m.start() - 24)
        end = min(len(blob), m.end() + 40)
        snippet = blob[start:end]
        # 取含错误词的短句
        line = re.split(r"[\n。；;]", snippet)[0].strip(" \t-:，,")
        if line:
            add(line[:80])

    # 中文/英文关键词兜底
    stop = {
        "the", "and", "for", "with", "from", "this", "that", "have", "been",
        "请", "帮我", "一下", "什么", "怎么", "如何", "问题", "分析", "日志",
        "看看", "为什么", "是否", "可能", "出现", "情况", "需要",
    }
    for m in _WORD_RE.finditer(blob):
        w = m.group(0)
        if w.lower() in stop or w.isdigit():
            continue
        if _ERR_RE.search(w) or len(w) >= 4:
            add(w)
        if len(found) >= max_n:
            break

    # 整段问题截断作为主 query
    first_line = blob.splitlines()[0].strip()
    if first_line:
        add(first_line[:100])

    return found[:max_n]


async def llm_rewrite(
    queries: list[str],
    *,
    user_text: str,
    target_n: int = 8,
    chat: dict | None = None,
) -> QueryGenResult:
    """用团队默认 Chat 扩写到约 target_n 条；失败则降级为规则结果.

    chat: {api_base, api_key, model, protocol}；为空则跳过 LLM。
    """
    base = list(queries)
    if not chat or not chat.get("api_key") or not chat.get("model"):
        return QueryGenResult(queries=base[:target_n], source="rule", note="")

    try:
        from app.modules.kb._chat import chat_completion
        prompt = (
            "你是运维诊断检索助手。根据用户问题与已有关键词，生成用于知识库检索的查询语句。\n"
            f"目标条数约 {target_n}，每行一条，不要编号与解释，覆盖：错误码、组件、现象、根因关键词。\n"
            f"用户问题：{user_text[:1500]}\n"
            f"已有关键词：{', '.join(base[:12]) or '（无）'}\n"
        )
        raw = await chat_completion(
            api_base=str(chat.get("api_base") or ""),
            api_key=str(chat["api_key"]),
            model=str(chat["model"]),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=600,
        )
        lines = [ln.strip(" -\t") for ln in (raw or "").splitlines() if ln.strip()]
        merged: list[str] = []
        for q in base + lines:
            q = re.sub(r"\s+", " ", q).strip()
            if len(q) < 2 or len(q) > 120:
                continue
            if any(q.lower() == x.lower() for x in merged):
                continue
            merged.append(q)
            if len(merged) >= target_n:
                break
        if not merged:
            return QueryGenResult(queries=base[:target_n], source="rule",
                                  note="LLM 未返回有效 query，已降级为规则抽取")
        return QueryGenResult(queries=merged, source="rule+llm", note="")
    except Exception as exc:  # noqa: BLE001
        logger.warning("kb llm_rewrite failed: %s", exc)
        return QueryGenResult(
            queries=base[:target_n], source="rule",
            note=f"LLM 改写失败，已降级为规则抽取：{exc}"[:200],
        )


async def build_queries(
    user_text: str,
    *,
    extras: list[str] | None = None,
    target_n: int = 8,
    chat: dict | None = None,
) -> QueryGenResult:
    rules = rule_extract(user_text, max_n=target_n, extras=extras)
    return await llm_rewrite(rules, user_text=user_text, target_n=target_n, chat=chat)
