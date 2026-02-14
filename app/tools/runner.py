"""
Local tool runner: resolve tool by id, build command from params, validate args, run subprocess.

- Uses same whitelist as CLIToolAdapter (alphanumeric, safe punctuation).
- No shell=True; command is list of arguments.
- Merges config local_tools with DB custom_abilities (custom overrides by id).
"""

import re
import shlex
import subprocess
from types import SimpleNamespace
from typing import Any

from app.config.schemas import LocalToolConfig, ModelsConfig

SAFE_PATTERN = re.compile(r"^[a-zA-Z0-9_\-./\s:=]+$")


def _tool_like(id: str, name: str, description: str, command: list[str] | str) -> LocalToolConfig | SimpleNamespace:
    """Something _build_command can use (has .command as list or str)."""
    return SimpleNamespace(id=id, name=name, description=description, command=command)


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


def _merged_tools(
    config: ModelsConfig,
    custom_abilities: list[dict[str, Any]] | None = None,
) -> list[Any]:
    """Merge config local_tools with custom_abilities (custom overrides by id). Returns list of tool-like objects."""
    by_id: dict[str, Any] = {}
    for t in getattr(config, "local_tools", None) or []:
        by_id[t.id] = t
    for c in custom_abilities or []:
        aid = c.get("id")
        if not aid:
            continue
        cmd = c.get("command")
        if isinstance(cmd, list):
            pass
        elif isinstance(cmd, str):
            cmd = shlex.split(cmd)
        else:
            cmd = ["true"]
        by_id[aid] = _tool_like(
            aid,
            c.get("name") or aid,
            c.get("description") or "",
            cmd,
        )
    return list(by_id.values())


def get_registered_tools(
    config: ModelsConfig,
    custom_abilities: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Return list of {id, name, description} for registered local tools (config + custom)."""
    tools = _merged_tools(config, custom_abilities)
    return [{"id": t.id, "name": t.name, "description": t.description or ""} for t in tools]


def resolve_tool(
    config: ModelsConfig,
    tool_id: str,
    custom_abilities: list[dict[str, Any]] | None = None,
) -> Any | None:
    """Resolve tool by id from config + custom. Returns tool-like object or None."""
    tools = _merged_tools(config, custom_abilities)
    return next((t for t in tools if t.id == tool_id), None)


def execute_local_tool(
    config: ModelsConfig,
    tool_id: str,
    params: dict[str, Any] | None = None,
    timeout_seconds: int = 30,
    custom_abilities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Execute a registered local tool by id with given params.
    Merges config local_tools with custom_abilities (custom overrides by id).

    Returns:
        {"stdout": str, "stderr": str, "returncode": int}
        On validation error: raises ValueError.
    """
    params = params or {}
    tool = resolve_tool(config, tool_id, custom_abilities)
    if not tool:
        raise ValueError(f"unknown tool: {tool_id}")

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
