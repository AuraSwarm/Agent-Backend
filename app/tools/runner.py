"""
Local tool runner: resolve tool by id, build command from params, validate args, run subprocess.

- Uses same whitelist as CLIToolAdapter (alphanumeric, safe punctuation).
- No shell=True; command is list of arguments.
"""

import re
import shlex
import subprocess
from typing import Any

from app.config.schemas import LocalToolConfig, ModelsConfig

SAFE_PATTERN = re.compile(r"^[a-zA-Z0-9_\-./\s:=]+$")


def _validate_args(args: list[str]) -> tuple[bool, str | None]:
    for a in args:
        if not SAFE_PATTERN.match(a):
            return False, f"invalid character in argument: {a!r}"
        if any(c in a for c in (";", "|", "&", "`", "$(", "\n", "\r")):
            return False, f"rejected shell metacharacter in: {a!r}"
    return True, None


def _build_command(tool: LocalToolConfig, params: dict[str, Any]) -> list[str]:
    """Build command list; substitute {key} from params. Params values must be str or safe."""
    if isinstance(tool.command, list):
        out = []
        for part in tool.command:
            try:
                out.append(part.format(**params) if params else part)
            except KeyError as e:
                raise ValueError(f"missing parameter: {e}") from e
        return out
    # Single string: format then split
    s = tool.command.format(**params) if params else tool.command
    return shlex.split(s)


def get_registered_tools(config: ModelsConfig) -> list[dict[str, str]]:
    """Return list of {id, name, description} for registered local tools."""
    return [
        {"id": t.id, "name": t.name, "description": t.description or ""}
        for t in (getattr(config, "local_tools", None) or [])
    ]


def execute_local_tool(
    config: ModelsConfig,
    tool_id: str,
    params: dict[str, Any] | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """
    Execute a registered local tool by id with given params.

    Returns:
        {"stdout": str, "stderr": str, "returncode": int}
        On validation error: raises ValueError.
    """
    params = params or {}
    tools = getattr(config, "local_tools", None) or []
    tool = next((t for t in tools if t.id == tool_id), None)
    if not tool:
        raise ValueError(f"unknown tool: {tool_id}")

    # Ensure param values are strings for formatting
    safe_params = {k: str(v) for k, v in params.items()}
    cmd = _build_command(tool, safe_params)
    ok, reason = _validate_args(cmd)
    if not ok:
        raise ValueError(reason or "invalid args")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    return {
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
        "returncode": result.returncode,
    }
