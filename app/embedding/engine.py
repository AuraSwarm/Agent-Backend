"""
Embedding engine: multi-provider support with fallback.

- Load provider from config (dashscope, openai, ...); session can override.
- On failure: try fallback provider if configured; log and return None so main flow continues.
"""

import os
from typing import Any

import httpx
import structlog

from app.config.loader import get_config

logger = structlog.get_logger(__name__)


async def get_embedding(
    text: str,
    provider: str | None = None,
    dimensions: int | None = None,
) -> list[float] | None:
    """
    Get embedding vector for text. Returns None on failure (caller can skip storing embedding).

    Args:
        text: Input text.
        provider: Provider key (default from config).
        dimensions: Optional dimension override.

    Returns:
        List of floats or None if all providers fail.
    """
    config = get_config()
    prov_name = provider or config.default_embedding_provider
    if not prov_name or prov_name not in config.embedding_providers:
        logger.warning("embedding_unknown_provider", provider=prov_name)
        return None

    providers_order = [prov_name]
    # Add fallback: if default is dashscope, try openai next
    for k in config.embedding_providers:
        if k != prov_name and k not in providers_order:
            providers_order.append(k)
            break

    for p in providers_order:
        prov = config.embedding_providers.get(p)
        if not prov:
            continue
        vec = await _call_embedding_provider(prov, text, dimensions)
        if vec:
            return vec
    return None


async def _call_embedding_provider(
    prov: Any,
    text: str,
    dimensions: int | None,
) -> list[float] | None:
    """Call one embedding API (DashScope/OpenAI style)."""
    api_key = os.environ.get(prov.api_key_env, "")
    if not api_key:
        raise ValueError(f"missing env {prov.api_key_env}")

    body: dict[str, Any] = {
        "model": prov.model,
        "input": text if isinstance(text, str) else [text],
    }
    if dimensions is not None:
        body["dimensions"] = dimensions
    elif getattr(prov, "dimensions", None):
        body["dimensions"] = prov.dimensions

    async with httpx.AsyncClient(timeout=prov.timeout) as client:
        r = await client.post(
            prov.endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
        )
        r.raise_for_status()
        data = r.json()
    # OpenAI/DashScope style: data["data"][0]["embedding"]
    items = data.get("data") or []
    if not items:
        return None
    return list(items[0].get("embedding") or [])
