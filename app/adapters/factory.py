"""Build chat adapter from provider config (cloud or claude_local)."""

from __future__ import annotations

from typing import Any

from app.adapters.base import BaseToolAdapter
from app.config.schemas import ChatProviderConfig


def build_chat_adapter(prov: ChatProviderConfig | Any, model: str) -> BaseToolAdapter:
    """Build CloudAPIAdapter or ClaudeLocalAdapter from provider config and model id."""
    ptype = getattr(prov, "type", None) or (prov.get("type") if isinstance(prov, dict) else None) or "cloud"
    if ptype == "claude_local":
        from app.adapters.claude_local import ClaudeLocalAdapter
        cmd = getattr(prov, "command", None) or (prov.get("command") if isinstance(prov, dict) else None)
        if not cmd:
            cmd = ["claude", "-p"]
        timeout = getattr(prov, "timeout", 120) or 120
        return ClaudeLocalAdapter(command=cmd, model=model, timeout=timeout)
    from app.adapters.cloud import CloudAPIAdapter
    api_key_env = getattr(prov, "api_key_env", None) or (prov.get("api_key_env") if isinstance(prov, dict) else "") or "DASHSCOPE_API_KEY"
    endpoint = (getattr(prov, "endpoint", None) or (prov.get("endpoint") if isinstance(prov, dict) else "") or "").rstrip("/")
    timeout = getattr(prov, "timeout", 60) or 60
    return CloudAPIAdapter(api_key_env=api_key_env, endpoint=endpoint, model=model, timeout=timeout)
