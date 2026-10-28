"""Schema 守卫: 幂等补充 create_all 无法处理的存量表变更.

create_all 只建新表, 不给已有表加列/不改枚举类型.
此处在 create_all 之前执行幂等原生 SQL (可重复运行):
- ALTER TYPE ... ADD VALUE IF NOT EXISTS (PG >= 12)
- ALTER TABLE ... ADD COLUMN IF NOT EXISTS

注意: ALTER TYPE 新增值与"使用该值"必须分属不同事务,
守卫单独提交, 种子数据在后续事务使用, 安全.

这是开发模式的过渡补丁, 生产环境请使用 Alembic 迁移 (见 docs/功能点清单.md).
"""
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# (SQL, 说明)
_GUARDS: list[tuple[str, str]] = [
    # tasks 表: analyze 任务扩展列 (witty 解析任务)
    ("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'analyze'", "tasktype 枚举加 analyze"),
    ("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'perf'", "tasktype 枚举加 perf (性能分析任务)"),
    ("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS name VARCHAR(128) DEFAULT ''", "tasks.name"),
    ("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS parser_type VARCHAR(32) DEFAULT ''", "tasks.parser_type"),
    ("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS team_id INTEGER REFERENCES teams(id)", "tasks.team_id"),
    ("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS log_size BIGINT DEFAULT 0", "tasks.log_size"),
    ("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS event_count INTEGER DEFAULT 0", "tasks.event_count"),
    # 去资产库: 回填归属团队后再删除归属列与资产库表 (幂等; 生产请用 Alembic 迁移)
    ("UPDATE tasks SET team_id = al.team_id FROM asset_libraries al WHERE tasks.asset_id = al.id",
     "tasks.team_id 回填 (经资产库)"),
    ("UPDATE tasks SET team_id = (params->>'team_id')::int "
     "WHERE team_id IS NULL AND params ? 'team_id' AND params->>'team_id' ~ '^[0-9]+$'",
     "tasks.team_id 回填 (经 params)"),
    ("ALTER TABLE tasks DROP COLUMN IF EXISTS asset_id", "tasks 去 asset_id"),
    ("DROP TABLE IF EXISTS asset_libraries", "删除 asset_libraries 表"),
    # users 表: 前端展示字段 (witty User 模型)
    ("ALTER TABLE users ADD COLUMN IF NOT EXISTS title VARCHAR(64) DEFAULT ''", "users.title"),
    ("ALTER TABLE users ADD COLUMN IF NOT EXISTS dept VARCHAR(64) DEFAULT ''", "users.dept"),
    ("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_hue INTEGER DEFAULT 212", "users.avatar_hue"),
    # rules 表: 归属团队 (NULL=平台通用规则, 非空=团队专属规则)
    ("ALTER TABLE rules ADD COLUMN IF NOT EXISTS team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE", "rules.team_id"),
    ("CREATE INDEX IF NOT EXISTS ix_rules_team_id ON rules (team_id)", "rules.team_id 索引"),
    # 团队角色合并: 创建者 (owner) 与团队管理员合并为 team_admin (幂等)
    ("UPDATE team_members SET role = 'team_admin' WHERE role = 'owner'", "team_members 角色合并 owner -> team_admin"),
    # diagnosis_sessions 表: 团队归属与模型快照 (团队级模型改造)
    ("ALTER TABLE diagnosis_sessions ALTER COLUMN model TYPE VARCHAR(128)", "diagnosis_sessions.model 扩长"),
    ("ALTER TABLE diagnosis_sessions ADD COLUMN IF NOT EXISTS team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL", "diagnosis_sessions.team_id"),
    ("ALTER TABLE diagnosis_sessions ADD COLUMN IF NOT EXISTS provider VARCHAR(32) NOT NULL DEFAULT ''", "diagnosis_sessions.provider"),
    ("ALTER TABLE diagnosis_sessions ADD COLUMN IF NOT EXISTS model_name VARCHAR(128) NOT NULL DEFAULT ''", "diagnosis_sessions.model_name"),
    ("ALTER TABLE diagnosis_sessions ADD COLUMN IF NOT EXISTS reasoning VARCHAR(16) NOT NULL DEFAULT ''", "diagnosis_sessions.reasoning"),
    ("CREATE INDEX IF NOT EXISTS ix_diagnosis_sessions_team_id ON diagnosis_sessions (team_id)", "diagnosis_sessions.team_id 索引"),
    # 知识库: 团队 Rerank + 诊断会话勾选
    ("ALTER TABLE teams ADD COLUMN IF NOT EXISTS kb_rerank_config JSONB DEFAULT '{}'::jsonb", "teams.kb_rerank_config"),
    ("ALTER TABLE diagnosis_sessions ADD COLUMN IF NOT EXISTS selected_kb_ids JSONB DEFAULT '[]'::jsonb", "diagnosis_sessions.selected_kb_ids"),
    ("ALTER TABLE diagnosis_sessions ADD COLUMN IF NOT EXISTS kb_top_k INTEGER DEFAULT 10", "diagnosis_sessions.kb_top_k"),
]


async def run_schema_guard(engine: AsyncEngine) -> None:
    """幂等执行存量表结构补丁, 单独事务提交."""
    applied = 0
    for sql, desc in _GUARDS:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql))
            applied += 1
        except Exception as e:  # noqa: BLE001
            # 新库首次启动时 tasktype 枚举可能尚不存在 (由 create_all 创建), 跳过即可
            logger.debug("schema 守卫跳过 %s: %s", desc, e)
    if applied:
        logger.info("schema 守卫: 已检查 %d 项", len(_GUARDS))
