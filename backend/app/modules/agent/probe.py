"""读取模型可选的推理强度: 启动短生命周期 ACP 会话, 用 DSH 目录能力回答."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from pathlib import Path

from app.core.config import get_settings
from app.modules.agent.acp import AcpConnection, AcpError, build_env
from app.modules.agent.providers import spec_of

logger = logging.getLogger(__name__)
settings = get_settings()

_cache: dict[tuple[str, str, str], dict] = {}


async def _noop(_update: dict) -> None:
    return None


def _probe_dir(provider: str, model: str) -> Path:
    digest = hashlib.sha1(model.encode()).hexdigest()[:12]
    return (Path(settings.DATA_DIR) / "probe" / f"{provider}-{digest}").resolve()


async def reasoning_options(provider: str, model: str, api_base: str = "") -> dict:
    """返回 {levels:[{id,name}], default}; 模型不支持推理时 levels 为空."""
    spec = spec_of(provider)
    model = (model or "").strip()
    api_base = (api_base or "").strip()
    if not model:
        return {"levels": [], "default": ""}
    key = (spec.id, api_base, model)
    if key in _cache:
        return _cache[key]
    command = settings.AGENT_ACP_CMD if spec.native == "deepseek" else settings.AGENT_ACP_MULTI_CMD
    if shutil.which(command) is None:
        return {"levels": [], "default": ""}
    ws = _probe_dir(spec.id, model)
    conn = AcpConnection(
        command=command,
        cwd=ws,
        env=build_env(ws, api_key="probe", model=model, provider=spec.id,
                      model_name=model, api_base=api_base),
        on_update=_noop,
    )
    try:
        await conn.start()
        res = await conn.new_session_result(ws, ready_timeout=25)
        # DSH 不同版本用 effort / reasoning_effort
        option = next(
            (o for o in res.get("configOptions") or []
             if o.get("id") in ("effort", "reasoning_effort")),
            None,
        )
        levels = [
            {"id": str(o.get("value") or ""), "name": o.get("name") or str(o.get("value") or "")}
            for o in (option or {}).get("options") or []
        ]
        out = {"levels": levels, "default": str((option or {}).get("currentValue") or "")}
        if levels:
            _cache[key] = out
        return out
    except AcpError as exc:
        logger.info("读取推理强度失败 %s/%s: %s", spec.id, model, exc)
        return {"levels": [], "default": ""}
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取推理强度异常 %s/%s: %s", spec.id, model, exc)
        return {"levels": [], "default": ""}
    finally:
        await conn.close()
        shutil.rmtree(ws, ignore_errors=True)
