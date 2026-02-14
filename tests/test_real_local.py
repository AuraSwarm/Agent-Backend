"""
Real integration tests for local tools and cursor-agent (no mocks).

- cursor-agent: requires `agent` in PATH (Cursor CLI).
- local tools (echo, date): use config from CONFIG_DIR / config/models.yaml.

Run: pytest tests/test_real_local.py -v
Run only real_local: pytest tests/test_real_local.py -m real_local -v
"""

import shutil

import pytest

from app.config.loader import get_config
from app.tools.runner import execute_local_tool


@pytest.mark.real_local
def test_execute_local_tool_echo_real():
    """Real execution: execute_local_tool(config, echo, {message})."""
    config = get_config()
    result = execute_local_tool(config, "echo", {"message": "real_test_hello"})
    assert result["returncode"] == 0
    assert "real_test_hello" in result["stdout"]


@pytest.mark.real_local
def test_execute_local_tool_date_real():
    """Real execution: execute_local_tool(config, date, {})."""
    config = get_config()
    result = execute_local_tool(config, "date", {})
    assert result["returncode"] == 0
    assert result["stdout"].strip()


@pytest.mark.asyncio
@pytest.mark.real_local
async def test_cursor_agent_real():
    """Real cursor-agent call: ClaudeLocalAdapter with command [agent, -p], no mock."""
    if not shutil.which("agent"):
        pytest.skip("agent not in PATH (install Cursor CLI)")
    from app.adapters.claude_local import ClaudeLocalAdapter

    adapter = ClaudeLocalAdapter(command=["agent", "-p"], model="cursor-local", timeout=120)
    prompt = "这是一个api 连接测试对话，请只回复测试通过"
    content, usage = await adapter.call(prompt)
    assert content is not None and isinstance(content, str)
    assert content.strip()
    assert usage == {}
