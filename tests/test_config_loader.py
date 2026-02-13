"""Tests for config loader: env injection, validation, reload, abilities merge."""

import os
from pathlib import Path

import pytest

from app.config.loader import (
    load_models_config,
    _substitute_env,
    _parse_abilities_yaml,
    _merge_aura_abilities_into_data,
)
from app.config.schemas import ModelsConfig


def test_substitute_env_string():
    os.environ["FOO"] = "bar"
    assert _substitute_env("hello ${FOO}") == "hello bar"
    assert _substitute_env("$FOO") == "bar"
    os.environ.pop("FOO", None)


def test_substitute_env_nested():
    os.environ["KEY"] = "secret"
    data = {"a": "${KEY}", "b": [{"c": "$KEY"}]}
    out = _substitute_env(data)
    assert out["a"] == "secret"
    assert out["b"][0]["c"] == "secret"
    os.environ.pop("KEY", None)


def test_load_models_config_invalid_yaml(tmp_path):
    (tmp_path / "models.yaml").write_text("embedding_providers: [invalid")
    with pytest.raises(Exception):
        load_models_config(str(tmp_path))


def test_load_models_config_empty(tmp_path):
    (tmp_path / "models.yaml").write_text("{}")
    cfg = load_models_config(str(tmp_path))
    assert isinstance(cfg, ModelsConfig)
    assert cfg.embedding_providers == {}
    assert cfg.summary_strategies == {}


def test_load_models_config_valid(test_config_dir):
    cfg = load_models_config(test_config_dir)
    assert "dashscope" in cfg.embedding_providers
    assert cfg.embedding_providers["dashscope"].dimensions == 1536
    assert cfg.default_embedding_provider == "dashscope"


# --- Abilities (cursor, copilot_cli, claude) from ability repo ---

ABILITIES_YAML_WITH_CLI = """
local_tools:
  - id: echo
    name: Echo
    description: Print message
    command: ["echo", "{message}"]
  - id: cursor
    name: Cursor CLI
    description: Run Cursor CLI with prompt
    command: ["agent", "-p", "{prompt}"]
  - id: copilot_cli
    name: Copilot CLI
    description: Run GitHub Copilot CLI
    command: ["copilot", "-p", "{prompt}"]
  - id: claude
    name: Claude CLI
    description: Run Claude CLI
    command: ["claude", "-p", "{prompt}"]
  - id: cursor_loop
    name: Cursor CLI (Loop)
    description: Cursor iterative
    command: ["agent", "-p", "{prompt}"]
  - id: copilot_cli_loop
    name: Copilot CLI (Loop)
    description: Copilot iterative
    command: ["copilot", "-p", "{prompt}"]
  - id: claude_loop
    name: Claude CLI (Loop)
    description: Claude continue session
    command: ["claude", "-c", "-p", "{prompt}"]
"""


def test_parse_abilities_yaml_returns_cli_abilities():
    """_parse_abilities_yaml parses YAML and returns list including cursor, copilot_cli, claude and loop variants."""
    parsed = _parse_abilities_yaml(ABILITIES_YAML_WITH_CLI)
    assert isinstance(parsed, list)
    ids = {a["id"] for a in parsed if isinstance(a, dict) and a.get("id")}
    assert "cursor" in ids
    assert "copilot_cli" in ids
    assert "claude" in ids
    assert "cursor_loop" in ids
    assert "copilot_cli_loop" in ids
    assert "claude_loop" in ids
    assert "echo" in ids
    by_id = {a["id"]: a for a in parsed}
    assert "-c" in by_id["claude_loop"].get("command", [])


def test_merge_aura_abilities_into_data_adds_cli_abilities(tmp_path, monkeypatch):
    """_merge_aura_abilities_into_data merges abilities file into data['local_tools'] by id."""
    ab_file = tmp_path / "abilities.yaml"
    ab_file.write_text(ABILITIES_YAML_WITH_CLI, encoding="utf-8")
    data = {"local_tools": [{"id": "echo", "name": "Echo", "description": "", "command": ["echo", "{message}"]}]}

    def _resolve(_config_dir):
        return ab_file

    monkeypatch.setattr("app.config.loader._resolve_abilities_file_path", _resolve)
    _merge_aura_abilities_into_data(data, config_dir=str(tmp_path))
    tools = data["local_tools"]
    ids = {t["id"] for t in tools if isinstance(t, dict) and t.get("id")}
    assert "cursor" in ids
    assert "copilot_cli" in ids
    assert "claude" in ids
    assert "claude_loop" in ids
    by_id = {t["id"]: t for t in tools}
    assert by_id["cursor"]["command"] == ["agent", "-p", "{prompt}"]
    assert by_id["claude"]["command"] == ["claude", "-p", "{prompt}"]
    assert by_id["claude_loop"]["command"] == ["claude", "-c", "-p", "{prompt}"]
