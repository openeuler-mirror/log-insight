"""诊断侧检索编排: query → search → 写 kb_context.md → 返回引用."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.agent.service import workspace_dir
from app.modules.kb.query_gen import build_queries
from app.modules.kb.search import Hit, search_kbs

logger = logging.getLogger(__name__)


@dataclass
class Citation:
    index: int
    chunk_id: int
    document_id: int
    kb_id: int
    kb_name: str
    title: str
    content: str
    score: float


@dataclass
class RetrieveResult:
    citations: list[Citation]
    queries: list[str]
    query_source: str = "rule"
    note: str = ""
    context_path: str = ""


async def retrieve_for_diagnosis(
    db: AsyncSession,
    *,
    team_id: int,
    session_id: str,
    selected_kb_ids: list[int],
    user_text: str,
    top_k: int = 10,
    chat: dict | None = None,
    extras: list[str] | None = None,
) -> RetrieveResult:
    """未选库则空结果且不写文件（无提示）."""
    if not selected_kb_ids:
        return RetrieveResult(citations=[], queries=[])

    settings = get_settings()
    if not settings.KB_ENABLED:
        return RetrieveResult(citations=[], queries=[], note="知识库未启用")

    qres = await build_queries(
        user_text,
        extras=extras,
        target_n=settings.KB_DEFAULT_QUERY_COUNT,
        chat=chat,
    )
    if not qres.queries:
        qres.queries = [user_text.strip()[:100]] if user_text.strip() else []

    hits = await search_kbs(
        db,
        team_id=team_id,
        kb_ids=selected_kb_ids,
        queries=qres.queries,
        top_k=top_k or settings.KB_DEFAULT_TOP_K,
        mode="auto",
    )
    if hits:
        from app.models.kb import KbDocument
        from sqlalchemy import select
        doc_ids = {h.document_id for h in hits}
        titles = {
            d.id: d.title
            for d in (await db.execute(
                select(KbDocument).where(KbDocument.id.in_(doc_ids))
            )).scalars().all()
        }
        for h in hits:
            if not h.title:
                h.title = titles.get(h.document_id, "")

    citations = [
        Citation(
            index=i + 1,
            chunk_id=h.chunk_id,
            document_id=h.document_id,
            kb_id=h.kb_id,
            kb_name=h.kb_name,
            title=h.title or f"chunk-{h.chunk_id}",
            content=h.content,
            score=round(float(h.score), 4),
        )
        for i, h in enumerate(hits)
    ]

    path = ""
    if citations:
        path = write_kb_context(session_id, citations, queries=qres.queries)
    else:
        # 清空旧上下文，避免沿用上轮结果
        _clear_kb_context(session_id)

    return RetrieveResult(
        citations=citations,
        queries=qres.queries,
        query_source=qres.source,
        note=qres.note,
        context_path=path,
    )


def write_kb_context(
    session_id: str,
    citations: list[Citation],
    *,
    queries: list[str] | None = None,
) -> str:
    settings = get_settings()
    ws = workspace_dir(session_id)
    dest = ws / "kb_context.md"
    lines = [
        "# 知识库检索结果",
        "",
        f"共 {len(citations)} 条引用。回答问题时请优先参考下文，并在结论中用 [n] 标注引用编号。",
        "",
    ]
    if queries:
        lines += ["检索 query：", *[f"- {q}" for q in queries[:12]], ""]

    budget = settings.KB_CONTEXT_MAX_CHARS
    used = sum(len(x) for x in lines)
    for c in citations:
        block = [
            f"## [{c.index}] {c.title} · {c.kb_name or f'库{c.kb_id}'}",
            f"chunk_id={c.chunk_id} score={c.score}",
            "",
            c.content.strip(),
            "",
        ]
        piece = "\n".join(block)
        if used + len(piece) > budget:
            remain = max(0, budget - used - 80)
            if remain > 100:
                lines.append(f"## [{c.index}] {c.title}（截断）")
                lines.append(c.content.strip()[:remain] + "…")
                lines.append("")
            lines.append("_后续引用因长度限制省略_")
            break
        lines.append(piece)
        used += len(piece)

    dest.write_text("\n".join(lines), encoding="utf-8")
    return str(dest)


def _clear_kb_context(session_id: str) -> None:
    try:
        p = Path(workspace_dir(session_id)) / "kb_context.md"
        if p.exists():
            p.unlink()
    except OSError:
        pass


def citations_as_dicts(citations: list[Citation]) -> list[dict]:
    return [asdict(c) for c in citations]


def hits_fill_titles(hits: list[Hit], titles: dict[int, str]) -> None:
    for h in hits:
        if not h.title:
            h.title = titles.get(h.document_id, "")
