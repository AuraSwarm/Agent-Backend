"""Audit log model for tool calls, config reload, CLI denials."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
