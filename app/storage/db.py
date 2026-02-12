"""
Database engine, session factory, and audit logging (delegated to memory_base).

- Sets default database_url from app config on init_db so routers can use get_session_factory() without passing URL.
- Ensures app-specific models (e.g. CodeReview) are registered with Base.metadata before init_db.
"""

import structlog
from memory_base import set_database_url
from memory_base.db import (
    get_engine as _get_engine,
    get_session_factory as _get_session_factory,
    init_db as _init_db,
    log_audit as _log_audit,
    session_scope as _session_scope,
)

from app.config.loader import get_app_settings

# Import so CodeReview is registered with memory_base.Base.metadata before init_db
from app.storage import models  # noqa: F401

logger = structlog.get_logger(__name__)


def get_engine():
    """Create or return async engine (uses default URL set at init)."""
    _ensure_url()
    return _get_engine()


def get_session_factory():
    """Return async session factory (uses default URL set at init)."""
    _ensure_url()
    return _get_session_factory()


async def init_db() -> None:
    """Create pgvector extension and tables. Sets default database_url from app config."""
    settings = get_app_settings()
    url = settings.database_url
    try:
        host_port = url.split("@", 1)[1].split("/")[0] if "@" in url else "localhost"
    except Exception:
        host_port = "localhost"
    logger.info("db_engine_creating", host_port=host_port)
    set_database_url(url)
    await _init_db()
    logger.info("db_init_done")


def session_scope():
    """Async context manager for a single DB session."""
    return _session_scope()


def log_audit(session, action: str, resource_type: str, resource_id=None, details=None):
    """Write an audit log entry. Caller commits the session."""
    return _log_audit(session, action, resource_type, resource_id=resource_id, details=details)


def _ensure_url() -> None:
    """Set default database URL from app config if not yet set."""
    if get_app_settings().database_url:
        set_database_url(get_app_settings().database_url)
    # else memory_base will raise when get_engine/get_session_factory are used