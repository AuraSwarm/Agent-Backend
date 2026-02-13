"""
App-specific and re-exported models.

- CodeReview: Agent-Backend feature (saved code review results); uses memory_base.Base.
- Re-exports Session, Message, SessionSummary, SessionStatus from memory_base for backward compatibility.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from memory_base.base import Base
from memory_base.models import Message, Session, SessionStatus, SessionSummary


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CodeReview(Base):
    """Saved code review result for history (list + detail)."""

    __tablename__ = "code_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)  # path, git, uncommitted
    path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    commits: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)  # for mode=git
    uncommitted_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    report: Mapped[str] = mapped_column(Text, nullable=False)
    files_included: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)  # for list display

    __table_args__ = (Index("ix_code_reviews_created_at", "created_at"),)


class CustomAbility(Base):
    """Web-managed ability (id, name, description, command, optional prompt_template). Merged with config local_tools for GET /api/abilities and tool execution."""

    __tablename__ = "custom_abilities"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    # command: list of args (e.g. ["echo", "{message}"] or ["date"]); unused when prompt_template is set
    command: Mapped[list] = mapped_column(JSONB, nullable=False)
    # 提示词能力：非空时执行该能力即用此模板（可含 {message}）调 LLM，返回结果作为能力输出
    prompt_template: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = ["Session", "Message", "SessionSummary", "SessionStatus", "CodeReview", "CustomAbility"]
