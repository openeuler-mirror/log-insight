"""官方 DSH ACP 运行时冒烟测试 (默认跳过).

需要本机可用的 `dsh`、可访问的模型凭据, 以及 harness/ 目录:

    LOGINSIGHT_ACPSMOKE=1 DEEPSEEK_API_KEY=sk-... \
        pytest backend/tests/agent/test_acp_smoke.py -s

未设置 LOGINSIGHT_ACPSMOKE 时不参与常规测试 (CI 无需 dsh)。
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

pytestmark = pytest.mark.skipif(
    os.environ.get("LOGINSIGHT_ACPSMOKE") != "1",
    reason="需要本机 dsh 运行时与模型凭据；设 LOGINSIGHT_ACPSMOKE=1 开启",
)

LOG = """2026-11-17 10:00:01 INFO  app started
2026-11-17 10:01:12 ERROR db connection timeout host=10.0.0.7
2026-11-17 10:02:00 ERROR circuit breaker OPEN service=orders
2026-11-17 10:02:30 INFO  recovered
"""


def _load_service(tmp_path: Path):
    os.environ.setdefault("AGENT_ACP_CMD", "dsh --profile acp")
    os.environ["AGENT_HARNESS_DIR"] = str(ROOT / "harness")
    os.environ["DATA_DIR"] = str(tmp_path / "data")
    os.environ.setdefault("AGENT_PERMISSION_MODE", "workspace-write")
    os.environ.setdefault("AGENT_AUTO_APPROVE", "true")
    os.environ.setdefault("AGENT_TIMEOUT_SECONDS", "240")

    import app.core.config as config

    config.get_settings.cache_clear()
    import app.modules.agent.service as service

    return importlib.reload(service)


def test_agent_reads_mounted_log(tmp_path: Path) -> None:
    service = _load_service(tmp_path)
    source = tmp_path / "app.log"
    source.write_text(LOG, encoding="utf-8")

    events = []

    async def run() -> None:
        async for event in service.stream_prompt(
            session_id="acp-smoke",
            dsh_session_id="",
            text="读取 uploads 下的 app.log，只回复 ERROR 行的数量（一个数字）。",
            attachments=[service.Attachment(
                task_id="t1", task_name="冒烟日志", name="app.log", abs_path=source)],
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            model=os.environ.get("AGENT_SMOKE_MODEL", "deepseek-flash"),
            provider=os.environ.get("AGENT_SMOKE_PROVIDER", "deepseek"),
            protocol=os.environ.get("AGENT_SMOKE_PROTOCOL", "messages"),
        ):
            events.append(event)

    asyncio.run(run())

    errors = [e["message"] for e in events if e["type"] == "error"]
    assert not errors, errors
    tools = [e for e in events if e["type"] == "tool"]
    assert tools, "智能体应调用工具读取日志"
    text = "".join(e.get("text", "") for e in events if e["type"] == "text")
    assert "2" in text, text[:200]
    assert any(e["type"] == "done" for e in events)
