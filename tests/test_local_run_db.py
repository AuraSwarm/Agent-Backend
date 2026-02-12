"""Tests for local run: DB required at startup, lifespan exits when DB unavailable."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.main import lifespan

# Path to run script (project root relative to tests/)
RUN_SCRIPT = Path(__file__).resolve().parent.parent / "run"


async def _run_lifespan(app):
    async with lifespan(app):
        pass


def test_init_db_is_called_in_lifespan():
    """Lifespan must call init_db so the app never runs without DB in local mode."""
    init_mock = AsyncMock(return_value=None)
    with patch("app.storage.db.init_db", side_effect=init_mock), patch(
        "app.main.validate_required_env"
    ), patch("app.main.start_config_watcher"):
        app = object()
        asyncio.run(_run_lifespan(app))
        init_mock.assert_called_once()


def test_lifespan_raises_system_exit_when_db_connection_refused():
    """When PostgreSQL is not reachable, lifespan must exit with a clear message."""
    err = ConnectionRefusedError(111, "Connection refused")
    with patch("app.storage.db.init_db", AsyncMock(side_effect=err)), patch(
        "app.main.validate_required_env"
    ):
        app = object()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(_run_lifespan(app))
        msg = str(exc_info.value)
        assert "PostgreSQL" in msg or "postgres" in msg.lower()
        assert "Cannot connect" in msg or "connection refused" in msg.lower() or "111" in msg


def test_lifespan_system_exit_message_suggests_fix_not_skip():
    """DB connection error message must suggest fixing DB (systemctl, pg_ctl, docker), not skipping."""
    err = ConnectionRefusedError(111, "Connection refused")
    with patch("app.storage.db.init_db", AsyncMock(side_effect=err)), patch(
        "app.main.validate_required_env"
    ):
        app = object()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(_run_lifespan(app))
        msg = str(exc_info.value)
        assert "Fix the database" in msg or "systemctl" in msg or "docker" in msg
        assert "SKIP_DB_WAIT" not in msg


def test_lifespan_raises_system_exit_on_oserror_connection_refused():
    """OSError with errno 111 (connection refused) is handled like ConnectionRefusedError."""
    err = OSError(111, "Connection refused")
    with patch("app.storage.db.init_db", AsyncMock(side_effect=err)), patch(
        "app.main.validate_required_env"
    ):
        app = object()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(_run_lifespan(app))
        msg = str(exc_info.value)
        assert "PostgreSQL" in msg or "postgres" in msg.lower()
        assert "Cannot connect" in msg or "111" in msg


def test_run_db_wait_uses_same_url_as_app(tmp_path):
    """run script exports DATABASE_URL from CONFIG_DIR/app.yaml; app uses same for init_db."""
    (tmp_path / "app.yaml").write_text("""
database_url: "postgresql+asyncpg://u:p@myhost:5434/mydb"
config_dir: "config"
""")
    import os
    import app.config.loader as loader
    loader._app_settings = None
    try:
        os.environ["CONFIG_DIR"] = str(tmp_path)
        from app.config.loader import get_app_settings
        settings = get_app_settings()
        url = settings.database_url
        assert "myhost" in url and "5434" in url and "mydb" in url
        # run script parses DB_HOST/DB_PORT from this URL; app uses same url for init_db
        host = url.split("@", 1)[1].split("/")[0].rsplit(":", 1)[0] if "@" in url else ""
        port = url.split("@", 1)[1].split("/")[0].rsplit(":", 1)[-1] if "@" in url else ""
        assert host == "myhost"
        assert port == "5434"
    finally:
        os.environ.pop("CONFIG_DIR", None)
        loader._app_settings = None


def test_run_local_mode_tries_multiple_ways_to_start_postgres():
    """In local mode run script tries system, pg_ctl, project-local dir, then Docker (when systemctl not allowed)."""
    if not RUN_SCRIPT.exists():
        pytest.skip("run script not found")
    text = RUN_SCRIPT.read_text()
    assert "_try_start_postgres_system" in text
    assert "_try_start_postgres_pg_ctl" in text
    assert "_try_start_postgres_local_dir" in text
    assert "_try_start_postgres_docker" in text
    assert "systemctl start postgresql" in text or "systemctl start postgres" in text
    assert "pg_ctl" in text
    assert "initdb" in text
    assert ".local/postgres-data" in text
    assert "docker compose up -d postgres" in text or "docker-compose up -d postgres" in text
