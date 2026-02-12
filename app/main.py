"""
FastAPI gateway: lifespan, request logging, and API routers.

Routers: health (models, health, admin), sessions, chat, tools, code_review.
Web UI: when WEB_UI_DIR is set (e.g. by Aura to Web-Service/static), serve / and /static from it.
"""

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config.loader import reload_config, start_config_watcher, validate_required_env
from app import storage
from app.routers import chat, code_review, health, sessions, tools

logger = structlog.get_logger(__name__)

# When Aura sets WEB_UI_DIR to Web-Service/static, serve the web UI from there
STATIC_DIR = Path(os.environ.get("WEB_UI_DIR", "")).resolve() if os.environ.get("WEB_UI_DIR") else None
if STATIC_DIR and not STATIC_DIR.is_dir():
    STATIC_DIR = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: validate env, init DB, config watcher. Shutdown: close."""
    logger.info("startup_start")
    validate_required_env()
    logger.info("env_validated")
    try:
        logger.info("init_db_start")
        await storage.db.init_db()
        logger.info("init_db_done")
    except (ConnectionRefusedError, OSError) as e:
        if getattr(e, "errno", None) == 111 or "connection refused" in str(e).lower():
            from app.config.loader import get_app_settings
            settings = get_app_settings()
            url = settings.database_url
            host_part = url.split("@", 1)[1].split("/")[0] if "@" in url else "localhost:5432"
            logger.error("postgres_connection_refused", detail=str(e), host_port=host_part)
            raise SystemExit(
                f"Cannot connect to PostgreSQL at {host_part}. "
                "Fix the database before starting: systemctl start postgresql (or brew services start postgresql), or ./run docker. "
                "Debug: pg_isready -h <host> -p <port> or systemctl status postgresql."
            ) from e
        raise

    def on_config_change(_event: str, path: str) -> None:
        reload_config()
        logger.info("config_reloaded", path=path)

    start_config_watcher(on_config_change)
    logger.info("config_watcher_started")
    logger.info("application_ready")
    yield
    logger.info("shutdown_start")


app = FastAPI(title="Agent Backend", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log each request (method, path, status, duration)."""
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = (time.monotonic() - start) * 1000
    logger.info(
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round(duration_ms, 2),
    )
    return response


# Include routers (no prefix so URLs stay /health, /sessions, /chat, /tools, /code-review*, /code-reviews*)
app.include_router(health.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(tools.router)
app.include_router(code_review.router)


# Optional web UI (when WEB_UI_DIR is set, e.g. by Aura pointing to Web-Service/static)
if STATIC_DIR is not None:
    @app.get("/")
    async def index():
        """Serve the chat web UI from WEB_UI_DIR."""
        index_file = STATIC_DIR / "index.html"
        if not index_file.exists():
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Static files not found")
        return FileResponse(index_file)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
