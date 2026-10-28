"""文档入库: 解析 → 切块 → 写库 (向量化见 embed, 可后续补)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.kb import KbChunk, KbDocument, KnowledgeBase
from app.modules.kb.chunking import split_chunks
from app.modules.kb.parsers import parse_file

logger = logging.getLogger(__name__)


async def ingest_document(db: AsyncSession, doc_id: int) -> None:
    doc = await db.get(KbDocument, doc_id)
    if doc is None or doc.deleted_at is not None:
        return
    kb = await db.get(KnowledgeBase, doc.kb_id)
    if kb is None or kb.deleted_at is not None:
        doc.status = "failed"
        doc.metadata_ = {**(doc.metadata_ or {}), "error": "知识库不存在或已删除"}
        await db.commit()
        return

    doc.status = "parsing"
    await db.commit()
    try:
        path = Path(doc.storage_path)
        text = doc.content_norm.strip() or parse_file(path)
        doc.content_norm = text
        settings = get_settings()
        size = int((kb.settings or {}).get("chunk_size") or settings.KB_CHUNK_SIZE)
        overlap = int((kb.settings or {}).get("chunk_overlap") or settings.KB_CHUNK_OVERLAP)
        parts = split_chunks(text, size=size, overlap=overlap)

        # 清旧块
        old = (await db.execute(select(KbChunk).where(KbChunk.document_id == doc.id))).scalars().all()
        for c in old:
            await db.delete(c)
        await db.flush()

        new_chunks: list[KbChunk] = []
        for i, content in enumerate(parts):
            ch = KbChunk(
                document_id=doc.id, kb_id=doc.kb_id, team_id=doc.team_id,
                chunk_index=i, content=content, token_count=len(content),
                embedding=[], metadata_={},
            )
            db.add(ch)
            new_chunks.append(ch)
        await db.flush()
        if kb.vector_enabled and new_chunks:
            from app.modules.kb.embed import embed_chunks_for_doc
            await embed_chunks_for_doc(db, kb, new_chunks)
        doc.status = "ready"
        doc.updated_at = datetime.now(timezone.utc)
        await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.exception("kb ingest failed doc=%s", doc_id)
        doc.status = "failed"
        doc.metadata_ = {**(doc.metadata_ or {}), "error": str(e)[:500]}
        await db.commit()
