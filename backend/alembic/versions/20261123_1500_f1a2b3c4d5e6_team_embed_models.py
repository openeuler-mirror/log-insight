"""team embed models + knowledge base binding

Revision ID: f1a2b3c4d5e6
Revises: e8a1b2c3d4f5
Create Date: 2026-11-23 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e8a1b2c3d4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS team_embed_models (
            id SERIAL PRIMARY KEY,
            team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            display_name VARCHAR(64) NOT NULL,
            model VARCHAR(128) NOT NULL,
            api_base VARCHAR(256) DEFAULT '',
            api_key_enc TEXT DEFAULT '',
            key_hint VARCHAR(16) DEFAULT '',
            is_default BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_team_embed_models_team_id ON team_embed_models (team_id)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_team_embed_default "
        "ON team_embed_models (team_id) WHERE is_default"
    )
    op.execute(
        "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS embed_model_id "
        "INTEGER REFERENCES team_embed_models(id) ON DELETE RESTRICT"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_bases_embed_model_id "
        "ON knowledge_bases (embed_model_id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS embed_model_id")
    op.execute("DROP TABLE IF EXISTS team_embed_models")
