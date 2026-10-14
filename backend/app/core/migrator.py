"""启动时自动数据库迁移: alembic upgrade head 的程序化入口.

env.py 内置 PG advisory lock, API 多 worker 与多 Worker 副本同时启动安全。
command.upgrade 是同步 API 且 env.py 内部用 asyncio.run, 故放工作线程执行。
"""
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

_BACKEND_DIR = Path(__file__).resolve().parents[2]  # .../backend


def _upgrade_head() -> None:
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.attributes["configure_logger"] = False  # 不动主进程日志配置
    command.upgrade(cfg, "head")


async def upgrade_database() -> None:
    """应用启动时调用; 已是 head 时为 no-op."""
    await asyncio.to_thread(_upgrade_head)
