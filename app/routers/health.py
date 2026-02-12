"""Health, models, and admin endpoints."""

from fastapi import APIRouter
from sqlalchemy import text

from app.config.loader import get_config, reload_config
from app.storage.db import get_session_factory, log_audit, session_scope

router = APIRouter(tags=["health"])


@router.get("/models")
async def list_models() -> dict:
    """Return available chat model ids from config."""
    config = get_config()
    providers = getattr(config, "chat_providers", {}) or {}
    default_name = config.default_chat_provider or "dashscope"
    if default_name not in providers:
        return {"models": [], "default": None}
    prov = providers[default_name]
    models = getattr(prov, "models", None) or [prov.model]
    return {"models": models, "default": prov.model}


@router.get("/health")
async def health() -> dict:
    """DB status for readiness probe."""
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}


@router.post("/admin/reload")
async def admin_reload() -> dict:
    """Hot reload config from disk."""
    reload_config()
    async with session_scope() as session:
        await log_audit(session, "reload_config", "config", details={"source": "admin"})
    return {"status": "ok", "message": "config reloaded"}
