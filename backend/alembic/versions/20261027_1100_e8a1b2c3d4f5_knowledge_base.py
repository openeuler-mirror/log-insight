"""knowledge base tables + team rerank + diagnosis kb fields

Revision ID: e8a1b2c3d4f5
Revises: d5f1a7c3e9b2
Create Date: 2026-10-27 11:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e8a1b2c3d4f5"
down_revision: Union[str, Sequence[str], None] = "d5f1a7c3e9b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE teams ADD COLUMN IF NOT EXISTS kb_rerank_config JSONB DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE diagnosis_sessions ADD COLUMN IF NOT EXISTS selected_kb_ids JSONB DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE diagnosis_sessions ADD COLUMN IF NOT EXISTS kb_top_k INTEGER DEFAULT 10")

    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(512), server_default=""),
        sa.Column("case_template", sa.Text(), server_default=""),
        sa.Column("vector_enabled", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("embedding_config", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("settings", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), server_default="active"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_knowledge_bases_team_id", "knowledge_bases", ["team_id"])

    op.create_table(
        "kb_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kb_id", sa.Integer(), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(256), server_default=""),
        sa.Column("doc_type", sa.String(32), server_default="raw_import"),
        sa.Column("ingest_mode", sa.String(16), server_default="raw"),
        sa.Column("source_format", sa.String(16), server_default=""),
        sa.Column("storage_path", sa.String(512), server_default=""),
        sa.Column("content_norm", sa.Text(), server_default=""),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), server_default="pending"),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_kb_documents_kb_id", "kb_documents", ["kb_id"])
    op.create_index("ix_kb_documents_team_id", "kb_documents", ["team_id"])

    op.create_table(
        "kb_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kb_id", sa.Integer(), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), server_default="0"),
        sa.Column("content", sa.Text(), server_default=""),
        sa.Column("embedding", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")),
        sa.Column("token_count", sa.Integer(), server_default="0"),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_kb_chunks_document_id", "kb_chunks", ["document_id"])
    op.create_index("ix_kb_chunks_kb_id", "kb_chunks", ["kb_id"])
    op.create_index("ix_kb_chunks_team_id", "kb_chunks", ["team_id"])

    op.create_table(
        "kb_ingest_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kb_id", sa.Integer(), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mode", sa.String(16), server_default="raw"),
        sa.Column("status", sa.String(16), server_default="pending"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "kb_ingest_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("kb_ingest_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(256), server_default=""),
        sa.Column("storage_path", sa.String(512), server_default=""),
        sa.Column("parse_status", sa.String(16), server_default="pending"),
        sa.Column("convert_status", sa.String(16), server_default=""),
        sa.Column("preview_markdown", sa.Text(), server_default=""),
        sa.Column("error_message", sa.Text(), server_default=""),
        sa.Column("selected", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("kb_documents.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("kb_ingest_items")
    op.drop_table("kb_ingest_batches")
    op.drop_table("kb_chunks")
    op.drop_table("kb_documents")
    op.drop_table("knowledge_bases")
