"""通过供应商的 /models 接口读取真实模型 id 与显示名称 (不发起推理)."""
from __future__ import annotations

import logging

import httpx

from app.modules.agent.providers import pretty_name, spec_of

logger = logging.getLogger(__name__)


def _urls(provider: str, base: str) -> list[str]:
    base = base.rstrip("/")
    if provider == "anthropic":
        root = base if base.endswith("/v1") else f"{base}/v1"
        return [f"{root}/models?limit=1000"]
    if provider == "google":
        return [f"{base}/models?pageSize=1000"]
    urls = [f"{base}/models"]
    if not base.endswith("/v1"):
        urls.append(f"{base}/v1/models")
    return urls


def _headers(provider: str, protocol: str, api_key: str) -> dict[str, str]:
    spec = spec_of(provider)
    if spec.auth == "google":
        return {"x-goog-api-key": api_key} if api_key else {}
    anthropic_auth = spec.auth == "anthropic" or (provider == "custom" and protocol == "messages")
    if anthropic_auth:
        headers = {"anthropic-version": "2023-06-01"}
        if api_key:
            headers["x-api-key"] = api_key
        return headers
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _parse(payload, provider: str = "") -> list[dict]:
    items = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(items, dict):
        items = items.get("models") or items.get("data") or []
    if not items and isinstance(payload, dict):
        items = payload.get("models") or []
    out: list[dict] = []
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        methods = item.get("supportedGenerationMethods")
        if provider == "google" and isinstance(methods, list) and "generateContent" not in methods:
            continue
        model_id = str(item.get("id") or item.get("model") or item.get("name") or "").strip()
        if model_id.startswith("models/"):
            model_id = model_id[len("models/"):]
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        raw_name = str(item.get("name") or "").strip()
        if raw_name.startswith("models/"):
            raw_name = raw_name[len("models/"):]
        name = str(item.get("display_name") or item.get("displayName") or "").strip()
        if not name and raw_name != model_id:
            name = raw_name
        out.append({"id": model_id, "name": name or pretty_name(model_id)})
    return out[:300]


async def discover_models(*, provider: str, api_key: str, api_base: str = "",
                          protocol: str = "chat-completions") -> dict:
    """返回 {ok, message, models:[{id,name}]}; 401/403 视为密钥问题."""
    spec = spec_of(provider)
    base = (api_base or spec.discovery_base).strip().rstrip("/")
    if not base:
        return {"ok": False, "message": "请填写接口地址", "models": []}
    if not api_key and not (spec.auth == "bearer" and provider == "openrouter"):
        return {"ok": False, "message": "请先填写 API Key", "models": []}

    headers = _headers(provider, protocol, api_key)
    last_status = 0
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for url in _urls(provider, base):
                resp = await client.get(url, headers=headers)
                last_status = resp.status_code
                if resp.status_code == 200:
                    try:
                        models = _parse(resp.json(), provider)
                    except ValueError:
                        models = []
                    if models:
                        return {"ok": True, "message": f"已读取 {len(models)} 个模型",
                                "models": models}
                    return {"ok": True, "message": "接口未返回模型列表，可直接手动填写",
                            "models": []}
                if resp.status_code in (401, 403):
                    return {"ok": False, "message": "密钥无效或没有权限", "models": []}
                if resp.status_code == 404:
                    continue
                if resp.status_code >= 500:
                    break
    except httpx.HTTPError as exc:
        logger.info("模型发现请求失败: %s", exc)
        return {"ok": False, "message": "连接失败：请检查接口地址与网络", "models": []}
    return {"ok": False, "message": f"服务返回 {last_status or '未知状态'}", "models": []}
