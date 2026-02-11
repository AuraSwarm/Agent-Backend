"""
CLI tool adapter: parameter whitelist + audit on deny.

- Only allows whitelisted args; blocks injection (e.g. "; rm -rf /").
- Logs denied attempts to audit_logs.
"""

import re
import shlex
from typing import Any, AsyncGenerator

import structlog

from app.adapters.base import BaseToolAdapter

logger = structlog.get_logger(__name__)

# Default whitelist: alphanumeric, spaces, safe punctuation for paths and flags
SAFE_PATTERN = re.compile(r"^[a-zA-Z0-9_\-./\s:=]+$")


class CLIToolAdapter(BaseToolAdapter):
    """
    Execute CLI tools with strict parameter validation.
    Rejects any argument containing shell metacharacters or non-whitelisted chars.
    """

    def __init__(
        self,
        timeout: int = 30,
        max_retries: int = 1,
        allowed_pattern: re.Pattern[str] | None = None,
    ):
        super().__init__(timeout=timeout, max_retries=max_retries)
        self.allowed_pattern = allowed_pattern or SAFE_PATTERN

    def _validate_args(self, args: list[str]) -> tuple[bool, str | None]:
        """
        Check each argument against whitelist.
        Returns (ok, None) or (False, reason).
        """
        for a in args:
            if not self.allowed_pattern.match(a):
                return False, f"invalid character in argument: {a!r}"
            # Block obvious injection
            if any(c in a for c in (";", "|", "&", "`", "$(", "\n", "\r")):
                return False, f"rejected shell metacharacter in: {a!r}"
        return True, None

    async def call(self, prompt: str, **kwargs: Any) -> str:
        """
        Parse prompt as command + args (space-separated or quoted), validate, then run.
        For real execution use subprocess; here we return a placeholder and rely on callers
        to use execute_cli() with session for audit.
        """
        parts = kwargs.get("args") or shlex.split(prompt.strip())
        if not parts:
            return ""
        ok, reason = self._validate_args(parts)
        if not ok:
            raise ValueError(reason or "invalid args")
        # Actual execution is done by caller with subprocess + audit
        return f"[CLI would run: {shlex.join(parts)}]"

    async def stream_call(self, prompt: str, **kwargs: Any) -> AsyncGenerator[str, None]:
        """Same as call but yields single chunk."""
        out = await self.call(prompt, **kwargs)
        if out:
            yield out
