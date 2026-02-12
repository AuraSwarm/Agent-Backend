"""
Long-term memory backend (Memory-Base): profiles and knowledge triples.

When config has OSS credentials (oss_endpoint, oss_bucket, oss_access_key_*),
backend uses Aliyun OSS. Otherwise uses in-memory storage for local dev.
Initialized at first use; Aura injects config so OSS is used when configured.
"""

from __future__ import annotations

from memory_base import create_long_term_backend_from_config
from memory_base.long_term_storage import LongTermStorageBackend, OssStorage

from app.config.loader import get_app_settings

_backend: LongTermStorageBackend | None = None


def get_long_term_backend() -> LongTermStorageBackend:
    """Return the long-term storage backend (OSS when configured, else in-memory). Lazy init."""
    global _backend
    if _backend is None:
        settings = get_app_settings()
        config = settings.model_dump(exclude_none=True)
        # Normalize OSS endpoint like Aura
        ep = config.get("oss_endpoint") or ""
        if ep and not str(ep).strip().startswith("http"):
            ep = ("https://" + str(ep).strip().rstrip("/")).strip()
            config["oss_endpoint"] = ep
        _backend = create_long_term_backend_from_config(config)
    return _backend


def is_long_term_oss() -> bool:
    """True when long-term storage is Aliyun OSS (not in-memory)."""
    return isinstance(get_long_term_backend(), OssStorage)


def reset_long_term_backend() -> None:
    """Reset cached backend (for tests)."""
    global _backend
    _backend = None
