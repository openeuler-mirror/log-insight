"""Alembic 迁移环境: async 模板改造.

- DATABASE_URL 复用 app.core.config (.env / 环境变量), 与运行态完全一致
- target_metadata 指向 Base.metadata, 支持后续 autogenerate
- 在线模式内置 PG 事务级 advisory lock, API 多 worker 与多 Worker 副本
  同时启动时迁移串行执行 (后到进程拿锁后发现已是 head, no-op)
"""
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app.core.config import get_settings
from app.core.database import Base
import app.models  # noqa: F401  注册全部 ORM 模型到 Base.metadata

config = context.config

# 复用应用配置覆盖 ini 中的空 url
config.set_main_option("sqlalchemy.url", get_settings().DATABASE_URL)

# 程序化调用 (app.core.migrator) 时不重配日志, 防止 fileConfig 干扰主进程 logger
if config.attributes.get("configure_logger", True):
    if config.config_file_name is not None:
        fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# 双进程并发防护锁 (固定魔数, 全链路唯一)
ADVISORY_LOCK_KEY = 7306151126


def run_migrations_offline() -> None:
    """离线模式: 仅生成 SQL 脚本, 不连库."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # 显式事务: advisory 锁与迁移 DDL 同事务; 块退出时提交 (锁自动释放)。
        # 注意不可让连接 autobegin 后裸跑 run_sync —— alembic 对"外部已开启的事务"
        # 不做提交, async 连接关闭时会回滚, 导致 DDL 静默丢失。
        async with connection.begin():
            await connection.execute(text(f"SELECT pg_advisory_xact_lock({ADVISORY_LOCK_KEY})"))
            await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
