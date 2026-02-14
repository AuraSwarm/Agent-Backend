"""Health, models, and admin endpoints."""

from fastapi import APIRouter
from sqlalchemy import text

from app.config.loader import get_config, reload_config
from app.storage.db import get_session_factory, log_audit, session_scope

router = APIRouter(tags=["health"])


def _models_list_from_config(config) -> list:
    """Merge all chat_providers' models (for GET /models so Web-Service dropdown includes Claude etc.)."""
    providers = getattr(config, "chat_providers", {}) or {}
    default_name = getattr(config, "default_chat_provider", None) or "dashscope"
    seen = set()
    result = []
    if default_name in providers:
        prov = providers[default_name]
        for m in getattr(prov, "models", None) or [getattr(prov, "model", "")] or []:
            if m and m not in seen:
                seen.add(m)
                result.append(m)
    for name, prov in providers.items():
        if name == default_name:
            continue
        for m in getattr(prov, "models", None) or [getattr(prov, "model", "")] or []:
            if m and m not in seen:
                seen.add(m)
                result.append(m)
    return result


@router.get("/models")
async def list_models() -> dict:
    """Return all chat model ids from config (all providers: dashscope, anthropic, claude-local, etc.)."""
    config = reload_config()
    providers = getattr(config, "chat_providers", {}) or {}
    default_name = getattr(config, "default_chat_provider", None) or "dashscope"
    default_model = None
    if default_name in providers:
        prov = providers[default_name]
        default_model = getattr(prov, "model", None)
    models = _models_list_from_config(config)
    return {"models": models, "default": default_model}


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
