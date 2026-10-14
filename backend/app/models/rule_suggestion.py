"""智能巡检规则推荐模型: 与任务一对一, 存储规则完善度评估与建议规则项."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RuleSuggestion(Base):
    """规则推荐结果 (不写入巡检报告, 独立存储便于用户编辑/复看)."""

    __tablename__ = "rule_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), unique=True, index=True
    )
    assessment: Mapped[str] = mapped_column(Text, default="")            # 规则完善度评估文本
    items: Mapped[list] = mapped_column(JSONB, default=list)             # 建议规则项 (可编辑)
    model: Mapped[str] = mapped_column(String(64), default="heuristic")  # 来源: heuristic 或 LLM 模型名
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )