"""tasks 去资产库: 归属改为 team_id

Revision ID: c4d8e2f6a1b3
Revises: b3c7d9e1f5a2
Create Date: 2026-10-14 15:30:00.000000

移除资产库 (asset_libraries) 概念:
- tasks 新增 team_id (FK -> teams.id) 直接表达任务归属团队;
- 存量数据回填: 解析任务沿 asset_libraries.team_id, 诊断/巡检任务取 params->>'team_id';
- 删除 tasks.asset_id 列与 asset_libraries 表。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4d8e2f6a1b3'
down_revision: Union[str, Sequence[str], None] = 'b3c7d9e1f5a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tasks', sa.Column('team_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_tasks_team_id'), 'tasks', ['team_id'], unique=False)
    op.create_foreign_key('fk_tasks_team_id_teams', 'tasks', 'teams', ['team_id'], ['id'])

    # 回填归属团队: 解析任务经资产库, 其余任务取 params.team_id
    op.execute("""
        UPDATE tasks SET team_id = al.team_id
        FROM asset_libraries al
        WHERE tasks.asset_id = al.id
    """)
    op.execute("""
        UPDATE tasks SET team_id = (params->>'team_id')::int
        WHERE team_id IS NULL AND params ? 'team_id'
          AND params->>'team_id' ~ '^[0-9]+$'
    """)

    op.drop_index(op.f('ix_tasks_asset_id'), table_name='tasks')
    op.drop_constraint('tasks_asset_id_fkey', 'tasks', type_='foreignkey')
    op.drop_column('tasks', 'asset_id')
    op.drop_index(op.f('ix_asset_libraries_team_id'), table_name='asset_libraries')
    op.drop_table('asset_libraries')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table('asset_libraries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('desc', sa.String(length=255), nullable=False),
        sa.Column('ip_range', sa.String(length=128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_asset_libraries_team_id'), 'asset_libraries', ['team_id'], unique=False)

    op.add_column('tasks', sa.Column('asset_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_tasks_asset_id'), 'tasks', ['asset_id'], unique=False)
    op.create_foreign_key('tasks_asset_id_fkey', 'tasks', 'asset_libraries', ['asset_id'], ['id'])

    op.drop_constraint('fk_tasks_team_id_teams', 'tasks', type_='foreignkey')
    op.drop_index(op.f('ix_tasks_team_id'), table_name='tasks')
    op.drop_column('tasks', 'team_id')