"""知识库检索: keyword + grep + vector → 可选团队 Rerank."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.kb import KbChunk, KnowledgeBase

logger = logging.getLogger(__name__)


@dataclass
class Hit:
    chunk_id: int
    document_id: int
    kb_id: int
    title: str
    content: str
    score: float
    kb_name: str = ""


async def search_kbs(
    db: AsyncSession,
    *,
    team_id: int,
    kb_ids: list[int],
    queries: list[str],
    top_k: int | None = None,
    mode: str = "auto",
) -> list[Hit]:
    if not kb_ids or not queries:
        return []
    settings = get_settings()
    top_k = top_k or settings.KB_DEFAULT_TOP_K
    recall = settings.KB_SEARCH_RECALL_PER_QUERY

    kbs = (await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.team_id == team_id,
            KnowledgeBase.id.in_(kb_ids),
            KnowledgeBase.deleted_at.is_(None),
            KnowledgeBase.status == "active",
        )
    )).scalars().all()
    if not kbs:
        return []
    kb_map = {k.id: k for k in kbs}
    ids = list(kb_map.keys())
    vector_ids = [k.id for k in kbs if k.vector_enabled]

    merged: dict[int, Hit] = {}
    for q in queries:
        q = (q or "").strip()
        if not q:
            continue
        do_kw = mode in ("auto", "keyword", "hybrid")
        do_grep = mode in ("auto", "grep", "hybrid")
        do_vec = mode in ("auto", "vector", "hybrid")
        if do_kw:
            for h in await _keyword(db, ids, q, recall):
                _merge(merged, h, kb_map)
        if do_grep:
            for h in await _grep(db, ids, q, recall):
                _merge(merged, h, kb_map)
        if do_vec and vector_ids:
            for h in await _vector(db, kb_map, vector_ids, q, recall):
                _merge(merged, h, kb_map)

    ranked = sorted(merged.values(), key=lambda h: h.score, reverse=True)
    # 召回放大后再统一 Rerank
    pool = ranked[: max(top_k * 3, recall)]
    try:
        from app.modules.kb.rerank import apply_team_rerank
        pool = await apply_team_rerank(db, team_id, queries[0] if queries else "", pool)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rerank skipped: %s", exc)
    return pool[:top_k]


def _merge(bucket: dict[int, Hit], hit: Hit, kb_map: dict) -> None:
    hit.kb_name = kb_map[hit.kb_id].name if hit.kb_id in kb_map else ""
    old = bucket.get(hit.chunk_id)
    if old is None or hit.score > old.score:
        bucket[hit.chunk_id] = hit


async def _keyword(db: AsyncSession, kb_ids: list[int], q: str, limit: int) -> list[Hit]:
    # 简单 ILIKE; 多词拆开 OR
    terms = [t for t in re.split(r"\s+", q) if t][:5] or [q]
    conds = [KbChunk.content.ilike(f"%{t}%") for t in terms]
    rows = (await db.execute(
        select(KbChunk).where(
            KbChunk.kb_id.in_(kb_ids),
            KbChunk.deleted_at.is_(None),
            or_(*conds),
        ).limit(limit)
    )).scalars().all()
    out: list[Hit] = []
    for c in rows:
        score = sum(1.0 for t in terms if t.lower() in c.content.lower()) / max(len(terms), 1)
        out.append(Hit(c.id, c.document_id, c.kb_id, "", c.content, score))
    return out


async def _grep(db: AsyncSession, kb_ids: list[int], q: str, limit: int) -> list[Hit]:
    """字面或正则; 非法正则则退回字面包含."""
    try:
        cre = re.compile(q, re.I)
        use_re = True
    except re.error:
        cre = None
        use_re = False

    # 先取候选再精筛 (库规模可控; 后续可加全文索引)
    rows = (await db.execute(
        select(KbChunk).where(
            KbChunk.kb_id.in_(kb_ids),
            KbChunk.deleted_at.is_(None),
        ).limit(max(limit * 20, 200))
    )).scalars().all()

    out: list[Hit] = []
    for c in rows:
        if use_re and cre is not None:
            if not cre.search(c.content):
                continue
            score = 1.2
        else:
            if q.lower() not in c.content.lower():
                continue
            score = 1.1
        out.append(Hit(c.id, c.document_id, c.kb_id, "", c.content, score))
        if len(out) >= limit:
            break
    return out


async def _vector(
    db: AsyncSession,
    kb_map: dict[int, KnowledgeBase],
    kb_ids: list[int],
    q: str,
    limit: int,
) -> list[Hit]:
    """JSONB 向量余弦相似度（无 pgvector 时的可用实现）."""
    from app.modules.kb.embed import cosine, embed_texts, resolve_embed_cred

    # 按库分别 embed query（不同库可能不同模型）
    out: list[Hit] = []
    for kid in kb_ids:
        kb = kb_map.get(kid)
        if kb is None:
            continue
        cred = await resolve_embed_cred(db, kb)
        if not cred:
            continue
        try:
            qvec = (await embed_texts([q], cred=cred))[0]
        except Exception as exc:  # noqa: BLE001
            logger.warning("query embed failed kb=%s: %s", kid, exc)
            continue
        rows = (await db.execute(
            select(KbChunk).where(
                KbChunk.kb_id == kid,
                KbChunk.deleted_at.is_(None),
            ).limit(500)
        )).scalars().all()
        scored: list[Hit] = []
        for c in rows:
            emb = c.embedding or []
            if not emb:
                continue
            scored.append(Hit(c.id, c.document_id, c.kb_id, "", c.content, cosine(qvec, emb) + 0.5))
        scored.sort(key=lambda h: h.score, reverse=True)
        out.extend(scored[:limit])
    return out
