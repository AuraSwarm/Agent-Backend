"""Tests for config loader: env injection, validation, reload."""

import os

import pytest

from app.config.loader import (
    load_models_config,
    _substitute_env,
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
