"""
Celery task: archive_by_activity().

- Sessions with updated_at < NOW() - 7 days and status=active are candidates.
- 7-180 days: move messages to messages_archive, update session status.
- 180-1095 days: export to Parquet in MinIO, mark status=archived.
- >1095 days: safe delete (audit log first).
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session as SyncSession, sessionmaker

from app.tasks.celery_app import celery_app
from app.config.loader import get_app_settings
from app.storage.base import Base
from app.storage import models_archive  # noqa: F401
from app.storage.models import SessionStatus

logger = structlog.get_logger(__name__)


def _sync_engine():
    """Sync engine for Celery (postgresql:// with psycopg2)."""
    url = get_app_settings().database_url
    sync_url = url.replace("postgresql+asyncpg", "postgresql")
    return create_engine(sync_url, pool_pre_ping=True)


def _session_scope():
    engine = _sync_engine()
    Session = sessionmaker(engine, expire_on_commit=False)
    return Session()


@celery_app.task(bind=True, name="tasks.archive_tasks.archive_by_activity")
def archive_by_activity(self: Any) -> dict[str, int]:
    """
    Archive sessions by activity (updated_at).
    Returns counts: cold_archived, parquet_exported, deleted.
    """
    db = _session_scope()
    try:
        # Ensure archive table exists
        Base.metadata.create_all(db.get_bind(), tables=[models_archive.MessageArchive.__table__])
        now = datetime.now(timezone.utc)
        cold_archived = _migrate_cold(db, now)
        parquet_exported = _export_parquet(db, now)
        deleted = _delete_old(db, now)
        db.commit()
        return {"cold_archived": cold_archived, "parquet_exported": parquet_exported, "deleted": deleted}
    except Exception as e:
        db.rollback()
        logger.exception("archive_by_activity_failed", error=str(e))
        raise
    finally:
        db.close()


def _migrate_cold(session: SyncSession, now: datetime) -> int:
    """Move messages from sessions (7-180 days inactive) to messages_archive."""
    cutoff_7 = now - timedelta(days=7)
    cutoff_180 = now - timedelta(days=180)
    # Select session ids in window
    r = session.execute(
        text("""
            SELECT id FROM sessions
            WHERE updated_at < :c7 AND updated_at >= :c180 AND status = :status_active
        """),
        {"c7": cutoff_7, "c180": cutoff_180, "status_active": SessionStatus.ACTIVE},
    )
    session_ids = [row[0] for row in r.fetchall()]
    count = 0
    for sid in session_ids:
        # Copy messages to archive (simplified: no embedding in archive)
        session.execute(
            text("""
                INSERT INTO messages_archive (id, session_id, role, content, created_at)
                SELECT id, session_id, role, content, created_at FROM messages WHERE session_id = :sid
            """),
            {"sid": sid},
        )
        session.execute(text("DELETE FROM messages WHERE session_id = :sid"), {"sid": sid})
        session.execute(
            text("UPDATE sessions SET status = :st WHERE id = :sid"),
            {"sid": sid, "st": SessionStatus.COLD_ARCHIVED},
        )
        count += 1
    return count


def _export_parquet(session: SyncSession, now: datetime) -> int:
    """180-1095 days: export to MinIO as Parquet (stub: just mark status=3)."""
    cutoff_180 = now - timedelta(days=180)
    cutoff_1095 = now - timedelta(days=1095)
    r = session.execute(
        text("""
            SELECT id FROM sessions
            WHERE updated_at < :c180 AND updated_at >= :c1095 AND status = :st_cold
        """),
        {"c180": cutoff_180, "c1095": cutoff_1095, "st_cold": SessionStatus.COLD_ARCHIVED},
    )
    ids = [row[0] for row in r.fetchall()]
    for sid in ids:
        session.execute(
            text("UPDATE sessions SET status = :st WHERE id = :sid"),
            {"sid": sid, "st": SessionStatus.DEEP_ARCHIVED},
        )
    # TODO: actual Parquet export to MinIO
    return len(ids)


def _delete_old(session: SyncSession, now: datetime) -> int:
    """>1095 days: safe delete (after audit); stub: just count and mark status=4."""
    cutoff = now - timedelta(days=1095)
    r = session.execute(
        text("SELECT id FROM sessions WHERE updated_at < :cut AND status = :st_deep"),
        {"cut": cutoff, "st_deep": SessionStatus.DEEP_ARCHIVED},
    )
    ids = [row[0] for row in r.fetchall()]
    for sid in ids:
        session.execute(
            text("UPDATE sessions SET status = :st WHERE id = :sid"),
            {"sid": sid, "st": SessionStatus.DELETED},
        )
    return len(ids)
