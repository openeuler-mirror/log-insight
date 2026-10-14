"""matchtype add script

Revision ID: a1e5f2b8c4d6
Revises: 34cf1514c199
Create Date: 2026-10-12 13:00:00.000000

脚本规则: matchtype 枚举新增 'script'。
单独成迁移的原因: PG 12+ 允许事务内 ALTER TYPE ADD VALUE,
但同一事务内不可使用新值, 与使用方(0003 及运行时)隔离。
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1e5f2b8c4d6'
down_revision: Union[str, Sequence[str], None] = '34cf1514c199'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # IF NOT EXISTS 保证重复执行幂等 (与 create_all 建出的库对齐场景)
    op.execute("ALTER TYPE matchtype ADD VALUE IF NOT EXISTS 'script'")


def downgrade() -> None:
    """PG 不支持删除枚举值, 降级为 no-op (新值不影响旧代码)."""
    pass
