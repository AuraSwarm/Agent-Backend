"""
Database engine, async session factory, and audit logging.

- Async engine and session for PostgreSQL (asyncpg).
- log_audit() for key operations (tool calls, config reload, etc.).
"""

import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator
from uuid import UUID

import structlog
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.loader import get_app_settings

logger = structlog.get_logger(__name__)
from app.storage.base import Base
from app.storage import models  # noqa: F401 - register Session, Message, SessionSummary with Base.metadata
from app.storage import models_audit  # noqa: F401 - register AuditLog with Base.metadata
from app.storage.models_audit import AuditLog

# Lazy init
_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    """Create or return async engine; ensure pgvector extension exists."""
    global _engine
    if _engine is None:
        settings = get_app_settings()
        url = settings.database_url
        host_port = url.split("@", 1)[1].split("/")[0] if "@" in url else "localhost"
        logger.info("db_engine_creating", host_port=host_port)
        _engine = create_async_engine(
            url,
            echo=False,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
        logger.info("db_engine_created")
    return _engine


async def init_db() -> None:
    """Create pgvector extension and tables (for init / tests)."""
    engine = get_engine()
    logger.info("db_extension_creating", extension="vector")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    logger.info("db_tables_creating")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("db_init_done")


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return async session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for a single DB session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def log_audit(
    session: AsyncSession,
    action: str,
    resource_type: str,
    resource_id: str | UUID | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """
    Write an audit log entry (e.g. tool call, config reload, denied CLI).
    Caller is responsible for committing the session.
    """
    await session.execute(
        insert(AuditLog.__table__).values(
            id=uuid.uuid4(),
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
            details=details,
        )
    )