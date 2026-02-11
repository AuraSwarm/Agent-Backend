"""
Local model adapter: Ollama / vLLM local endpoints.

- Same call/stream_call interface; endpoint typically http://localhost:11434 (Ollama).
"""

import json
from typing import Any, AsyncGenerator

import httpx
import structlog

from app.adapters.base import BaseToolAdapter

logger = structlog.get_logger(__name__)


class LocalModelAdapter(BaseToolAdapter):
    """Call local Ollama or vLLM-compatible endpoint."""

    def __init__(
        self,
        endpoint: str = "http://localhost:11434",
        model: str = "llama2",
        timeout: int = 120,
        max_retries: int = 2,
    ):
        super().__init__(timeout=timeout, max_retries=max_retries)
        self.endpoint = endpoint.rstrip("/")
        self.model = model

    def _body(self, prompt: str, stream: bool = False, **kwargs: Any) -> dict[str, Any]:
        return {
            "model": kwargs.get("model") or self.model,
            "prompt": prompt,
            "stream": stream,
            **{k: v for k, v in kwargs.items() if k not in ("model",)},
        }

    async def call(self, prompt: str, **kwargs: Any) -> str:
        """Non-streaming call."""
        body = self._body(prompt, stream=False, **kwargs)

        async def _do() -> str:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self.endpoint}/api/generate",
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                return data.get("response") or ""

        return await self._with_retry(lambda: _do())

    async def stream_call(self, prompt: str, **kwargs: Any) -> AsyncGenerator[str, None]:
        """Stream response (Ollama /api/generate with stream=True)."""
        body = self._body(prompt, stream=True, **kwargs)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", f"{self.endpoint}/api/generate", json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.strip().startswith("{"):
                        continue
                    data = json.loads(line)
                    chunk = data.get("response")
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        break
