"""CLI adapter: whitelist and injection rejection."""

import pytest
from app.adapters.cli import CLIToolAdapter


@pytest.mark.asyncio
async def test_cli_safe_args():
    adapter = CLIToolAdapter()
    out = await adapter.call("echo hello")
    assert "echo" in out and "hello" in out


@pytest.mark.asyncio
async def test_cli_rejects_semicolon():
    adapter = CLIToolAdapter()
    with pytest.raises(ValueError, match="rejected|metacharacter|invalid"):
        await adapter.call("; rm -rf /")


@pytest.mark.asyncio
async def test_cli_rejects_pipe():
    adapter = CLIToolAdapter()
    with pytest.raises(ValueError):
        await adapter.call("cat file | rm -rf /")
