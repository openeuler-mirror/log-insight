"""tasktype add perf

Revision ID: d5f1a7c3e9b2
Revises: c4d8e2f6a1b3
Create Date: 2026-10-21 10:00:00.000000

性能分析任务: tasktype 枚举新增 'perf'。
单独成迁移的原因: PG 12+ 允许事务内 ALTER TYPE ADD VALUE,
但同一事务内不可使用新值, 与使用方(运行时)隔离。
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd5f1a7c3e9b2'
down_revision: Union[str, Sequence[str], None] = 'c4d8e2f6a1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # IF NOT EXISTS 保证重复执行幂等 (与 create_all 建出的库对齐场景)
    op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'perf'")


def downgrade() -> None:
    """PG 不支持删除枚举值, 降级为 no-op (新值不影响旧代码)."""
    pass
