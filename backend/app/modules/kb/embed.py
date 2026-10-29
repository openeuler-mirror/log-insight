"""Embedding 客户端: OpenAI 兼容 /embeddings；优先使用库级独立 Key."""
from __future__ import annotations

import json
import logging
import math
from typing import Any
from urllib import error, request

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.models.kb import KnowledgeBase
from app.models.llm import TeamLlmProvider
from app.modules.agent.providers import spec_of

logger = logging.getLogger(__name__)


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


async def resolve_embed_cred(db: AsyncSession, kb: KnowledgeBase) -> dict | None:
    """解析 Embedding 凭据：库级 Key 优先，其次才回退团队 LLM 供应商."""
    if not kb.vector_enabled:
        return None
    cfg = kb.embedding_config or {}
    model = str(cfg.get("model") or "").strip()
    if not model:
        return None

    # 1) 库级独立 Key（建库时填写，可与 Chat LLM 不同）
    api_key = ""
    if cfg.get("api_key_enc"):
        api_key = decrypt_secret(str(cfg["api_key_enc"]))
    elif cfg.get("api_key"):
        api_key = str(cfg["api_key"])
    api_base = str(cfg.get("api_base") or "").strip()
    if api_key:
        return {"model": model, "api_key": api_key, "api_base": api_base}

    # 2) 兼容旧数据：回退团队供应商 / 平台 DeepSeek Key
    provider = str(cfg.get("provider") or "deepseek").strip() or "deepseek"
    settings = get_settings()
    row = (await db.execute(select(TeamLlmProvider).where(
        TeamLlmProvider.team_id == kb.team_id,
        TeamLlmProvider.provider == spec_of(provider).id,
        TeamLlmProvider.enabled.is_(True),
    ))).scalar_one_or_none()
    api_key = decrypt_secret(row.api_key_enc) if row and row.api_key_enc else ""
    if not api_key and provider == "deepseek":
        api_key = settings.AGENT_API_KEY or ""
    if not api_key:
        return None
    api_base = api_base or (row.api_base if row else "") or spec_of(provider).default_base or ""
    return {"model": model, "api_key": api_key, "api_base": str(api_base)}


async def embed_texts(texts: list[str], *, cred: dict) -> list[list[float]]:
    import asyncio
    if not texts:
        return []
    return await asyncio.to_thread(
        _embed_sync, texts, cred["api_base"], cred["api_key"], cred["model"])


def _embed_sync(texts: list[str], api_base: str, api_key: str, model: str) -> list[list[float]]:
    base = (api_base or "https://api.deepseek.com").rstrip("/")
    url = f"{base}/embeddings" if base.endswith("/v1") else f"{base}/v1/embeddings"
    body: dict[str, Any] = {"model": model, "input": texts}
    data = json.dumps(body).encode("utf-8")
    req = request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=90) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Embedding HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Embedding 连接失败: {exc.reason}") from exc

    items = sorted(payload.get("data") or [], key=lambda x: x.get("index", 0))
    out = [list(map(float, it.get("embedding") or [])) for it in items]
    if len(out) != len(texts):
        raise RuntimeError(f"Embedding 条数不匹配: expect {len(texts)} got {len(out)}")
    return out


async def embed_chunks_for_doc(db: AsyncSession, kb: KnowledgeBase, chunks: list) -> None:
    """批量为 chunk 写入 embedding；失败仅记日志，不阻断 ingest."""
    cred = await resolve_embed_cred(db, kb)
    if not cred:
        logger.info("kb=%s 跳过向量化（未配置 Embedding 凭据或模型）", kb.id)
        return
    batch = 16
    for i in range(0, len(chunks), batch):
        part = chunks[i:i + batch]
        try:
            vecs = await embed_texts([c.content for c in part], cred=cred)
        except Exception as exc:  # noqa: BLE001
            logger.warning("embed batch failed kb=%s: %s", kb.id, exc)
            return
        for c, v in zip(part, vecs):
            c.embedding = v
