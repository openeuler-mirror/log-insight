"""轻量 OpenAI 兼容 Chat Completions 调用（案例转换 / query 改写共用）."""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib import error, request

logger = logging.getLogger(__name__)


async def chat_completion(
    *,
    api_base: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 1200,
    timeout: float = 60,
) -> str:
    """同步 HTTP 包一层 async；失败抛异常."""
    import asyncio
    return await asyncio.to_thread(
        _chat_sync,
        api_base=api_base,
        api_key=api_key,
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )


def _chat_sync(
    *,
    api_base: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: float = 60,
) -> str:
    base = (api_base or "https://api.deepseek.com").rstrip("/")
    if base.endswith("/v1"):
        url = f"{base}/chat/completions"
    else:
        url = f"{base}/v1/chat/completions"
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(body).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Chat HTTP {exc.code}: {detail}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Chat 响应超时（>{int(timeout)}s），请稍后重试或减少单次文件数") from exc
    except error.URLError as exc:
        reason = str(exc.reason)
        if "timed out" in reason.lower() or "timeout" in reason.lower():
            raise RuntimeError(f"Chat 响应超时（>{int(timeout)}s），请稍后重试或减少单次文件数") from exc
        raise RuntimeError(f"Chat 连接失败: {exc.reason}") from exc

    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("Chat 无返回 choices")
    msg = choices[0].get("message") or {}
    content = msg.get("content") or ""
    if isinstance(content, list):
        content = "".join(
            (c.get("text") if isinstance(c, dict) else str(c)) for c in content
        )
    return str(content).strip()
