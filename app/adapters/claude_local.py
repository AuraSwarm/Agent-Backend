"""
Claude local adapter: chat via local Claude CLI (e.g. `claude -p "..."`) or Cursor CLI (`agent -p "..."`).

- Runs the configured command with the prompt; stdout is the assistant reply.
- Use in models.yaml as a chat_provider with type: claude_local.
- For cursor-local: command ["agent", "-p"]; ensure CURSOR_API_KEY is set and agent is in PATH.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from pathlib import Path
from typing import Any, AsyncGenerator

import structlog

from app.adapters.base import BaseToolAdapter

logger = structlog.get_logger(__name__)

# When prompt exceeds this size, pass via stdin to avoid ARG_MAX / command-line length limits
_STDIN_PROMPT_THRESHOLD = 64 * 1024

# Fallback paths for agent when not in PATH (e.g. backend started by systemd/IDE)
_AGENT_FALLBACK_PATHS = [Path.home() / ".local" / "bin" / "agent", Path("/usr/local/bin/agent")]


def _resolve_executable(first_arg: str) -> str:
    """Resolve executable to full path so subprocess finds it when PATH is limited."""
    if not first_arg or os.path.sep in first_arg:
        return first_arg
    resolved = shutil.which(first_arg)
    if resolved:
        return resolved
    if first_arg.lower() in ("agent", "cursor"):
        for p in _AGENT_FALLBACK_PATHS:
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
    return first_arg


def _format_messages(messages: list[dict[str, Any]] | None) -> str | None:
    """Turn messages (role/content) into a single prompt string for the CLI."""
    if not messages:
        return None
    parts = []
    for m in messages:
        role = (m.get("role") or "user").strip().lower()
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role == "system":
            parts.append(f"System: {content}")
        elif role == "user":
            parts.append(f"User: {content}")
        elif role == "assistant":
            parts.append(f"Assistant: {content}")
        else:
            parts.append(content)
    return "\n\n".join(parts) if parts else None


class ClaudeLocalAdapter(BaseToolAdapter):
    """Call local Claude CLI for chat; returns (content, usage) like CloudAPIAdapter."""

    def __init__(
        self,
        command: list[str] | str,
        model: str = "claude-local",
        timeout: int = 120,
        max_retries: int = 2,
    ):
        super().__init__(timeout=timeout, max_retries=max_retries)
        if isinstance(command, str):
            self._cmd = shlex.split(command)
        else:
            self._cmd = list(command)
        self.model = model

    def _is_cursor_agent(self) -> bool:
        """True if the command is Cursor CLI (agent), which supports --output-format text."""
        if not self._cmd:
            return False
        exe = (self._cmd[0] or "").lower()
        return "agent" in exe or exe.endswith("cursor")

    async def call(self, prompt: str, **kwargs: Any) -> tuple[str, dict]:
        """Run CLI with prompt; return (stdout as content, {})."""
        messages = kwargs.get("messages")
        text = (_format_messages(messages) if messages else None) or prompt
        text = (text or "").replace("\x00", "")  # avoid null bytes in argv or stdin
        # Cursor CLI (agent) expects prompt as argument; only use stdin for other CLIs when prompt is very long
        use_stdin = len(text) > _STDIN_PROMPT_THRESHOLD and not self._is_cursor_agent()
        if self._is_cursor_agent() and len(text) > _STDIN_PROMPT_THRESHOLD:
            text = "[前面内容已省略，仅保留最近部分]\n\n" + text[-(_STDIN_PROMPT_THRESHOLD - 80):]

        # Build argv: replace {message}/{prompt} in args, or append prompt (e.g. claude -p "<prompt>")
        argv = []
        substituted = False
        for part in self._cmd:
            if "{message}" in part or "{prompt}" in part:
                part = part.replace("{message}", text).replace("{prompt}", text)
                substituted = True
            argv.append(part)
        if not substituted and not use_stdin:
            # Cursor agent: --output-format text 必须在 prompt 之前，否则 "text" 可能被当成 prompt
            if self._is_cursor_agent() and "--output-format" not in argv:
                argv.extend(["--output-format", "text"])
            argv.append(text)
        elif self._is_cursor_agent() and "--output-format" not in argv:
            argv.extend(["--output-format", "text"])
        if argv and self._is_cursor_agent():
            argv[0] = _resolve_executable(argv[0])

        env = os.environ.copy()
        cwd = os.getcwd()

        async def _do() -> tuple[str, dict]:
            stdin_pipe = asyncio.subprocess.PIPE if use_stdin else None
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=stdin_pipe,
                env=env,
                cwd=cwd,
            )
            if use_stdin:
                proc.stdin.write(text.encode("utf-8", errors="replace"))
                proc.stdin.close()
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            out = (stdout or b"").decode("utf-8", errors="replace").strip()
            err = (stderr or b"").decode("utf-8", errors="replace").strip()
            if proc.returncode != 0 and err:
                logger.warning("claude_local_stderr", returncode=proc.returncode, stderr=err[:500])
            if proc.returncode != 0:
                raise RuntimeError(f"claude local exited {proc.returncode}: {err[:200] or out[:200]}")
            # Cursor agent 有时将回复写到 stderr；stdout 为空时用 stderr 作为内容
            if not out and err:
                logger.info("claude_local_stdout_empty_using_stderr", stderr_len=len(err), model=self.model)
                out = err
            if not out:
                logger.warning("claude_local_empty_response", argv_len=len(argv), model=self.model)
            return (out or "(no output)", {})

        return await self._with_retry(_do)

    async def stream_call(self, prompt: str, **kwargs: Any) -> AsyncGenerator[str | dict[str, Any], None]:
        """Non-streaming CLI: yield full response as one chunk. Always yield at least one content so frontend can clear 'thinking'."""
        content, usage = await self.call(prompt, **kwargs)
        if usage:
            yield {"_usage": usage}
        yield content if (content and content.strip()) else "(无输出)"
