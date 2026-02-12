"""
Cloud API adapter: Qwen/Claude/OpenAI-compatible chat API.

- Uses httpx for async HTTP; supports stream and json_mode.
"""

import json
import os
from typing import Any, AsyncGenerator

import httpx
import structlog

from app.adapters.base import BaseToolAdapter

logger = structlog.get_logger(__name__)


class CloudAPIAdapter(BaseToolAdapter):
    """Call cloud chat APIs (DashScope/Qwen, OpenAI, etc.) with streaming and retry."""

    def __init__(
        self,
        api_key_env: str,
        endpoint: str,
        model: str,
        timeout: int = 60,
        max_retries: int = 3,
    ):
        super().__init__(timeout=timeout, max_retries=max_retries)
        self.api_key_env = api_key_env
        self.endpoint = endpoint.rstrip("/")
        self.model = model

    def _headers(self) -> dict[str, str]:
        key = (os.environ.get(self.api_key_env) or "").strip()
        if not key:
            raise ValueError(
                f"API key not set: set {self.api_key_env} in environment or config (e.g. config/app.yaml)"
            )
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def _body(self, prompt: str, stream: bool = False, **kwargs: Any) -> dict[str, Any]:
        messages = kwargs.get("messages")
        if not messages:
            messages = [{"role": "user", "content": prompt}]
        return {
            "model": kwargs.get("model") or self.model,
            "messages": messages,
            "stream": stream,
            **{k: v for k, v in kwargs.items() if k not in ("messages", "model")},
        }

    async def call(self, prompt: str, **kwargs: Any) -> tuple[str, dict]:
        """Non-streaming call; returns (content, usage). usage has prompt_tokens, completion_tokens, total_tokens if provided by API."""
        body = self._body(prompt, stream=False, **kwargs)

        async def _do() -> tuple[str, dict]:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self.endpoint}/chat/completions",
                    headers=self._headers(),
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                choice = (data.get("choices") or [None])[0]
                content = ""
                if choice:
                    content = (choice.get("message") or {}).get("content") or ""
                usage = (data.get("usage") or {}).copy()
                return (content, usage)

        return await self._with_retry(lambda: _do())

    async def stream_call(
        self, prompt: str, **kwargs: Any
    ) -> AsyncGenerator[str | dict[str, Any], None]:
        """Stream response chunks. May yield content (str) or usage (dict with key '_usage')."""
        body = self._body(prompt, stream=True, **kwargs)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.endpoint}/chat/completions",
                headers=self._headers(),
                json=body,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    if line.strip() == "data: [DONE]":
                        break
                    data = json.loads(line[6:])
                    usage = data.get("usage")
                    if usage:
                        yield {"_usage": usage}
                        continue
                    delta = (data.get("choices") or [{}])[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content
