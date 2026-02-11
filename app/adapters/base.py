"""
Abstract base for tool adapters: Cloud API, Local model, CLI.

- call() and stream_call() with timeout, retry (exponential backoff), token counting.
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Callable

import structlog

logger = structlog.get_logger(__name__)


class BaseToolAdapter(ABC):
    """Abstract adapter for LLM/tool calls: cloud API, local (Ollama/vLLM), CLI."""

    def __init__(
        self,
        timeout: int = 60,
        max_retries: int = 3,
    ):
        self.timeout = timeout
        self.max_retries = max_retries

    @abstractmethod
    async def call(self, prompt: str, **kwargs: Any) -> str:
        """
        Single non-streaming call.

        Returns:
            Full response text.
        """
        ...

    @abstractmethod
    async def stream_call(self, prompt: str, **kwargs: Any) -> AsyncGenerator[str, None]:
        """
        Streaming call; yields chunks.

        Yields:
            Response text chunks.
        """
        ...

    async def _with_retry(self, coro_factory: Callable[[], Any]) -> Any:
        """Run async call once with timeout."""
        return await asyncio.wait_for(coro_factory(), timeout=self.timeout)

    def _estimate_tokens(self, text: str) -> int:
        """Rough token count (chars / 4). Override for model-specific logic."""
        return max(1, len(text) // 4)
