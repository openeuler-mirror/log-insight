"""团队级大模型配置与用量明细."""
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, DateTime, ForeignKey, Index, Integer,
                        String, Text, func, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TeamLlmProvider(Base):
    """团队与某个供应商的连接 (每个团队每个供应商一条)."""
    __tablename__ = "team_llm_providers"
    __table_args__ = (
        Index("uq_team_llm_provider", "team_id", "provider", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(64), default="")
    api_key_enc: Mapped[str] = mapped_column(Text, default="")
    key_hint: Mapped[str] = mapped_column(String(16), default="")
    api_base: Mapped[str] = mapped_column(String(256), default="")
    protocol: Mapped[str] = mapped_column(String(32), default="messages")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TeamLlmModel(Base):
    """团队启用的模型清单 (从供应商 /models 勾选, 可自定义显示名称)."""
    __tablename__ = "team_llm_models"
    __table_args__ = (
        Index("uq_team_llm_model", "team_id", "provider_id", "model_id", unique=True),
        Index("ix_team_llm_models_enabled", "team_id", "enabled", "sort_order"),
        Index("uq_team_llm_model_default", "team_id", unique=True,
              postgresql_where=text("is_default")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("team_llm_providers.id", ondelete="CASCADE"), index=True)
    model_id: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LlmUsageEvent(Base):
    """每轮诊断的用量明细 (按人/团队/模型可聚合)."""
    __tablename__ = "llm_usage_events"
    __table_args__ = (
        Index("ix_llm_usage_team_time", "team_id", "created_at"),
        Index("ix_llm_usage_team_user_time", "team_id", "user_id", "created_at"),
        Index("ix_llm_usage_team_model_time", "team_id", "model", "created_at"),
        Index("ix_llm_usage_session", "session_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("diagnosis_sessions.id", ondelete="CASCADE"),
        nullable=True)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    model_name: Mapped[str] = mapped_column(String(128), default="")
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cached_read_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cached_write_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    thought_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class DiagnosisUserTeamSelection(Base):
    """用户在某个团队下最后选择的模型 (切回该团队时恢复)."""
    __tablename__ = "diagnosis_user_team_selections"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    model_row_id: Mapped[int | None] = mapped_column(
        ForeignKey("team_llm_models.id", ondelete="SET NULL"), nullable=True)
    reasoning: Mapped[str] = mapped_column(String(16), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
