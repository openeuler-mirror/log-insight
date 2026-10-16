"""新增 knowledge_notes 个人知识库表

Revision ID: e7a1b2c3d4f5
Revises: c4d8e2f6a1b3
Create Date: 2026-10-16 17:00:00.000000

新增个人知识库: 用户私有的运维知识笔记, 严格按 owner_id 隔离.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e7a1b2c3d4f5'
down_revision: Union[str, Sequence[str], None] = 'c4d8e2f6a1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'knowledge_notes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('source_type', sa.String(length=32), nullable=False),
        sa.Column('source_task_id', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_knowledge_notes_owner_id'), 'knowledge_notes', ['owner_id'], unique=False)
    op.create_index('ix_knowledge_notes_owner_updated', 'knowledge_notes', ['owner_id', 'updated_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_knowledge_notes_owner_updated', table_name='knowledge_notes')
    op.drop_index(op.f('ix_knowledge_notes_owner_id'), table_name='knowledge_notes')
    op.drop_table('knowledge_notes')
