"""个人知识库: 用户私有的运维知识笔记 (案例/处置经验/排查思路)."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class KnowledgeNote(Base):
    """知识条目: 严格按 owner_id 隔离, 每人仅见自己的笔记."""
    __tablename__ = "knowledge_notes"
    __table_args__ = (Index("ix_knowledge_notes_owner_updated", "owner_id", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSONB, default=list)                    # 标签字符串数组
    source_type: Mapped[str] = mapped_column(String(32), default="manual")     # manual/diagnosis/inspection
    source_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
