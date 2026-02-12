"""
SQLAlchemy declarative models for sessions, messages, session_summaries.

- sessions: partition by RANGE(updated_at) for archival (hot 7d / cold 7-180d / deep 180-1095d).
- messages: include embedding VECTOR(1536) for semantic search.
- session_summaries: JSONB for structured summary (decision_points, todos, code_snippets, etc.).
"""

import uuid
from datetime import datetime, timezone
from typing import Any

# Session status for archival (used in archive_tasks and queries)
SessionStatus = type("SessionStatus", (), {"ACTIVE": 1, "COLD_ARCHIVED": 2, "DEEP_ARCHIVED": 3, "DELETED": 4})()


def _utc_now() -> datetime:
    """Timezone-aware UTC now (Python 3.12+ preferred over deprecated utcnow())."""
    return datetime.now(timezone.utc)

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pgvector.sqlalchemy import Vector

from app.storage.base import Base


class Session(Base):
    """
    Chat session; updated_at used for archival (hot 7d / cold 7-180d / deep 180-1095d).

    Can be converted to PARTITION BY RANGE (updated_at) via Alembic.
    status: 1=active, 2=archived (cold), 3=deep_archived (parquet/minio), 4=deleted.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )
    status: Mapped[int] = mapped_column(SmallInteger, default=SessionStatus.ACTIVE, nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)

    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan")
    summaries = relationship("SessionSummary", back_populates="session", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Session id={self.id} status={self.status}>"


class Message(Base):
    """Single message in a session; may hold embedding for semantic search."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # user, assistant, system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    session = relationship("Session", back_populates="messages")

    __table_args__ = (Index("ix_messages_session_id", "session_id"),)

    def __repr__(self) -> str:
        return f"<Message id={self.id} role={self.role}>"


class SessionSummary(Base):
    """Structured summary of a session (compression output); JSONB holds code_snippets, todos, etc."""

    __tablename__ = "session_summaries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. context_compression_v2
    strategy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # optional human-readable
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # decision_points, todos, code_snippets
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    session = relationship("Session", back_populates="summaries")

    __table_args__ = (Index("ix_session_summaries_session_id", "session_id"),)


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
