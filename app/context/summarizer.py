"""
Structured summary generation for context compression.

- Loads strategy from config (prompt_template, output_schema including code_snippets).
- Calls configured model (json_mode); validates output with Pydantic.
- On failure: keep original context + structured warning log.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel

from app.config.loader import get_config

logger = structlog.get_logger(__name__)


class CodeSnippet(BaseModel):
    """Single code snippet in summary."""

    language: str = ""
    code: str = ""
    purpose: str = ""


class SummaryOutput(BaseModel):
    """Validated summary output (matches config output_schema)."""

    decision_points: list[str] = []
    todos: list[str] = []
    entities: dict[str, Any] = {}
    context_state: str = ""
    code_snippets: list[CodeSnippet] = []

    model_config = {"extra": "allow"}


async def compress_context(
    history: str,
    strategy_name: str = "context_compression_v2",
    model_override: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Compress conversation history into structured JSON summary.

    Args:
        history: Raw conversation text.
        strategy_name: Key from config summary_strategies.
        model_override: Optional model name override.

    Returns:
        (summary_dict, None) on success; (None, error_message) on failure.
        On validation failure or model error, returns (None, msg) and logs; caller keeps original context.
    """
    config = get_config()
    strategies = config.summary_strategies
    if strategy_name not in strategies:
        return None, f"unknown strategy: {strategy_name}"

    strategy = strategies[strategy_name]
    # Replace only known placeholders so literal braces in template (e.g. {files: [...]}) are unchanged
    prompt = strategy.prompt_template.replace("{history}", history)
    for key, val in (("files", "[]"), ("tools", "[]")):
        if "{" + key + "}" in prompt:
            prompt = prompt.replace("{" + key + "}", val)
    model = model_override or strategy.model

    adapter = _get_adapter_for_model(model)
    result = await adapter.call(
        prompt,
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    response = result[0] if isinstance(result, tuple) else result

    import json
    raw = response.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    data = json.loads(raw)
    validated = SummaryOutput.model_validate(data)
    return validated.model_dump(), None


def _get_adapter_for_model(model: str):
    """Build chat adapter for given model from config chat_providers (cloud or claude_local)."""
    from app.adapters.factory import build_chat_adapter
    config = get_config()
    providers = getattr(config, "chat_providers", None) or {}
    prov_name, prov = None, None
    for name, p in providers.items():
        models = getattr(p, "models", None) or [getattr(p, "model", "")]
        if model in (models or []):
            prov_name, prov = name, p
            break
    if prov is None:
        default_provider = config.default_chat_provider or "dashscope"
        if default_provider not in providers:
            raise ValueError(f"default chat provider {default_provider} not in config")
        prov = providers[default_provider]
    return build_chat_adapter(prov, model)
