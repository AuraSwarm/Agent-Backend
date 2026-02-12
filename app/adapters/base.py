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
        """Run async call with timeout and exponential backoff retry (transient errors)."""
        last_exc: BaseException | None = None
        for attempt in range(max(1, self.max_retries)):
            try:
                return await asyncio.wait_for(coro_factory(), timeout=self.timeout)
            except (asyncio.TimeoutError, OSError, ConnectionError) as e:
                last_exc = e
                if attempt + 1 >= self.max_retries:
                    raise
                delay = 2 ** attempt
                logger.warning(
                    "adapter_retry",
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                    delay_seconds=delay,
                    error_type=type(e).__name__,
                )
                await asyncio.sleep(delay)
        if last_exc:
            raise last_exc
        raise RuntimeError("_with_retry exhausted")

    def _estimate_tokens(self, text: str) -> int:
        """Rough token count (chars / 4). Override for model-specific logic."""
        return max(1, len(text) // 4)
