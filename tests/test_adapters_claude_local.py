"""Claude local adapter and build_chat_adapter factory."""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.adapters.claude_local import ClaudeLocalAdapter, _format_messages
from app.adapters.factory import build_chat_adapter
from app.config.loader import load_models_config
from app.config.schemas import ChatProviderConfig


def test_format_messages():
    out = _format_messages([
        {"role": "system", "content": "Sys"},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Bye"},
    ])
    assert "System: Sys" in out
    assert "User: Hi" in out
    assert "Assistant: Bye" in out


def test_build_chat_adapter_cloud():
    prov = ChatProviderConfig(
        type="cloud",
        api_key_env="DASHSCOPE_API_KEY",
        endpoint="https://example.com/v1",
        model="qwen-max",
        timeout=60,
    )
    from app.adapters.cloud import CloudAPIAdapter
    adapter = build_chat_adapter(prov, "qwen-max")
    assert isinstance(adapter, CloudAPIAdapter)
    assert adapter.model == "qwen-max"
    assert adapter.endpoint == "https://example.com/v1"


def test_build_chat_adapter_claude_local():
    prov = ChatProviderConfig(
        type="claude_local",
        model="claude-local",
        timeout=120,
        command=["claude", "-p"],
    )
    adapter = build_chat_adapter(prov, "claude-local")
    assert isinstance(adapter, ClaudeLocalAdapter)
    assert adapter.model == "claude-local"
    assert adapter._cmd == ["claude", "-p"]


def test_build_chat_adapter_claude_local_default_command():
    prov = ChatProviderConfig(type="claude_local", model="claude-local")
    adapter = build_chat_adapter(prov, "claude-local")
    assert isinstance(adapter, ClaudeLocalAdapter)
    assert adapter._cmd == ["claude", "-p"]


@pytest.mark.asyncio
async def test_claude_local_call_mocked_subprocess():
    adapter = ClaudeLocalAdapter(command=["echo", "ok"], model="test", timeout=10)
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"hello from echo", b""))
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc) as m:
        content, usage = await adapter.call("hi")
    assert content == "hello from echo"
    assert usage == {}
    m.assert_called_once()
    call_args = m.call_args[0]
    assert call_args[0] == "echo"
    assert call_args[1] == "ok"
    assert call_args[2] == "hi"


@pytest.mark.asyncio
async def test_claude_local_call_with_placeholder():
    adapter = ClaudeLocalAdapter(command=["sh", "-c", "echo {message}"], model="test", timeout=10)
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"done", b""))
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc) as m:
        await adapter.call("hello world")
    call_args = m.call_args[0]
    assert call_args[0] == "sh"
    assert call_args[1] == "-c"
    assert "hello world" in call_args[2]


@pytest.mark.asyncio
async def test_claude_local_call_with_messages():
    adapter = ClaudeLocalAdapter(command=["echo", "x"], model="test", timeout=10)
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"out", b""))
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc) as m:
        await adapter.call("prompt", messages=[
            {"role": "system", "content": "Sys"},
            {"role": "user", "content": "User"},
        ])
    call_args = m.call_args[0]
    assert "System: Sys" in call_args[-1]
    assert "User: User" in call_args[-1]


@pytest.mark.asyncio
async def test_claude_local_call_nonzero_exit_raises():
    adapter = ClaudeLocalAdapter(command=["false"], model="test", timeout=10)
    proc = AsyncMock()
    proc.returncode = 1
    proc.communicate = AsyncMock(return_value=(b"", b"error"))
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
        with pytest.raises(RuntimeError, match="exited 1"):
            await adapter.call("hi")


@pytest.mark.asyncio
async def test_claude_local_integration_real_command(tmp_path: Path):
    """
    使用真实子进程完成与 claude-local 的对话：用脚本模拟本地 CLI，验证配置加载 -> 适配器 -> 调用 -> 返回。
    """
    fake_claude = tmp_path / "fake_claude"
    fake_claude.write_text('#!/usr/bin/env python3\nimport sys\nprint("OK")\nsys.exit(0)\n')
    fake_claude.chmod(0o755)
    models_yaml = tmp_path / "models.yaml"
    models_yaml.write_text(f"""
embedding_providers:
  dashscope:
    api_key_env: DASHSCOPE_API_KEY
    endpoint: https://example.com/embeddings
    model: text-embedding-v3
    dimensions: 1536
    timeout: 10
default_embedding_provider: dashscope
chat_providers:
  dashscope:
    api_key_env: DASHSCOPE_API_KEY
    endpoint: https://example.com/v1
    model: qwen-max
    timeout: 60
  claude-local:
    type: claude_local
    model: claude-local
    timeout: 30
    command: ["{fake_claude.resolve().as_posix()}"]
    models:
      - claude-local
default_chat_provider: claude-local
summary_strategies: {{}}
""")
    config = load_models_config(config_dir=str(tmp_path))
    assert "claude-local" in config.chat_providers
    prov = config.chat_providers["claude-local"]
    adapter = build_chat_adapter(prov, "claude-local")
    content, usage = await adapter.call("Say OK in one word.")
    assert "OK" in content
    assert usage == {}


@pytest.mark.asyncio
async def test_cursor_local_and_copilot_local_dialogue_via_fake_cli(tmp_path: Path):
    """
    测试与 cursor-local、copilot-local 的对话：用同一假脚本模拟 Cursor/Copilot CLI，
    验证配置中 command 不同（agent -p / copilot -p）时适配器均能正确调用并返回。
    """
    fake_cli = tmp_path / "fake_cli"
    fake_cli.write_text('#!/usr/bin/env python3\nimport sys\nprint("OK from local CLI")\nsys.exit(0)\n')
    fake_cli.chmod(0o755)
    path_str = fake_cli.resolve().as_posix()

    models_yaml = tmp_path / "models.yaml"
    models_yaml.write_text(f"""
embedding_providers:
  dashscope:
    api_key_env: DASHSCOPE_API_KEY
    endpoint: https://example.com/embeddings
    model: text-embedding-v3
    dimensions: 1536
    timeout: 10
default_embedding_provider: dashscope
chat_providers:
  dashscope:
    api_key_env: DASHSCOPE_API_KEY
    endpoint: https://example.com/v1
    model: qwen-max
    timeout: 60
  cursor-local:
    type: claude_local
    model: cursor-local
    timeout: 30
    command: ["{path_str}"]
    models: [cursor-local]
  copilot-local:
    type: claude_local
    model: copilot-local
    timeout: 30
    command: ["{path_str}"]
    models: [copilot-local]
default_chat_provider: dashscope
summary_strategies: {{}}
""")
    config = load_models_config(config_dir=str(tmp_path))

    for model_id in ("cursor-local", "copilot-local"):
        assert model_id in config.chat_providers
        prov = config.chat_providers[model_id]
        adapter = build_chat_adapter(prov, model_id)
        assert adapter.model == model_id
        content, usage = await adapter.call("Say hello in one word.")
        assert "OK" in content or "hello" in content.lower(), f"{model_id} should return script output"
        assert usage == {}
