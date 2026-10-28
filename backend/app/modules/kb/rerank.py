"""团队级 Rerank: 有配置则调用兼容 HTTP；无配置/失败则保留融合分排序."""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from urllib import error, request

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.models.team import Team

if TYPE_CHECKING:
    from app.modules.kb.search import Hit

logger = logging.getLogger(__name__)


async def apply_team_rerank(
    db: AsyncSession,
    team_id: int,
    query: str,
    hits: list[Hit],
) -> list[Hit]:
    if not hits or not query.strip():
        return hits
    team = await db.get(Team, team_id)
    cfg = (team.kb_rerank_config if team else None) or {}
    if not cfg.get("enabled") and not cfg.get("model"):
        return hits
    model = str(cfg.get("model") or "").strip()
    api_key = decrypt_secret(cfg["api_key_enc"]) if cfg.get("api_key_enc") else str(cfg.get("api_key") or "")
    api_base = str(cfg.get("api_base") or "").rstrip("/")
    if not model or not api_key:
        return hits

    docs = [h.content[:2000] for h in hits]
    try:
        import asyncio
        scores = await asyncio.to_thread(
            _rerank_sync, query, docs, api_base, api_key, model)
    except Exception as exc:  # noqa: BLE001
        logger.warning("team rerank failed: %s", exc)
        return hits

    if len(scores) != len(hits):
        return hits
    paired = list(zip(hits, scores))
    paired.sort(key=lambda x: x[1], reverse=True)
    out: list[Hit] = []
    for h, sc in paired:
        h.score = float(sc)
        out.append(h)
    return out


def _rerank_sync(
    query: str,
    documents: list[str],
    api_base: str,
    api_key: str,
    model: str,
) -> list[float]:
    """尽量兼容 OpenAI-like / Cohere-like rerank 接口."""
    base = api_base or "https://api.deepseek.com"
    if not base.endswith("/v1") and "/rerank" not in base:
        url = f"{base.rstrip('/')}/v1/rerank"
    elif base.endswith("/rerank"):
        url = base
    else:
        url = f"{base.rstrip('/')}/rerank"

    body = json.dumps({
        "model": model,
        "query": query,
        "documents": documents,
        "top_n": len(documents),
    }).encode("utf-8")
    req = request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Rerank HTTP {exc.code}: {detail}") from exc

    results = payload.get("results") or payload.get("data") or []
    scores = [0.0] * len(documents)
    for item in results:
        idx = item.get("index")
        if idx is None:
            continue
        sc = item.get("relevance_score", item.get("score", 0))
        try:
            scores[int(idx)] = float(sc)
        except (TypeError, ValueError, IndexError):
            continue
    return scores
