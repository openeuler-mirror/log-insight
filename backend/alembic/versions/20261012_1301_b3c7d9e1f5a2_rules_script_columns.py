"""rules script columns

Revision ID: b3c7d9e1f5a2
Revises: a1e5f2b8c4d6
Create Date: 2026-10-12 13:01:00.000000

脚本规则三列: script_path / script_params / script_timeout。
仅 script 类型使用; 存量行带默认值, 不回填。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b3c7d9e1f5a2'
down_revision: Union[str, Sequence[str], None] = 'a1e5f2b8c4d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('rules', sa.Column('script_path', sa.String(length=256),
                                     nullable=False, server_default=''))
    op.add_column('rules', sa.Column('script_params', postgresql.JSONB(astext_type=sa.Text()),
                                     nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column('rules', sa.Column('script_timeout', sa.Integer(),
                                     nullable=False, server_default='5'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('rules', 'script_timeout')
    op.drop_column('rules', 'script_params')
    op.drop_column('rules', 'script_path')
