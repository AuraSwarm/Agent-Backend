"""Tests for app config (config/app.yaml) and local start behavior."""

import os
from pathlib import Path

import pytest

from app.config.loader import (
    get_app_settings,
    _app_config_path,
    reset_app_settings_cache,
    validate_required_env,
)
from app.config.schemas import AppSettings


def test_app_config_path_uses_config_dir():
    """CONFIG_DIR env controls where app.yaml is looked up."""
    prev = os.environ.get("CONFIG_DIR")
    try:
        os.environ["CONFIG_DIR"] = "/etc/agent"
        p = _app_config_path()
        assert "agent" in str(p)
        assert p.name == "app.yaml"
    finally:
        if prev is not None:
            os.environ["CONFIG_DIR"] = prev
        else:
            os.environ.pop("CONFIG_DIR", None)


def test_app_config_path_default():
    """Default CONFIG_DIR is 'config'."""
    prev = os.environ.pop("CONFIG_DIR", None)
    try:
        p = _app_config_path()
        assert "config" in str(p)
        assert p.name == "app.yaml"
    finally:
        if prev is not None:
            os.environ["CONFIG_DIR"] = prev


def test_test_database_url_overrides_config(tmp_path):
    """When TEST_DATABASE_URL is set, get_app_settings() uses it for database_url (test DB isolation)."""
    (tmp_path / "app.yaml").write_text("""
database_url: "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_backend"
config_dir: "config"
""")
    test_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_backend_test"
    prev_env = {}
    for key in ("CONFIG_DIR", "TEST_DATABASE_URL"):
        prev_env[key] = os.environ.pop(key, None)
    try:
        os.environ["CONFIG_DIR"] = str(tmp_path)
        os.environ["TEST_DATABASE_URL"] = test_url
        reset_app_settings_cache()
        settings = get_app_settings()
        assert settings.database_url == test_url
    finally:
        for k, v in prev_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        reset_app_settings_cache()


def test_test_oss_and_minio_bucket_override_config(tmp_path):
    """When TEST_OSS_BUCKET / TEST_MINIO_BUCKET are set, get_app_settings() uses them (test bucket isolation)."""
    (tmp_path / "app.yaml").write_text("""
database_url: "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_backend"
oss_bucket: "aura-mem"
minio_bucket: "archives"
config_dir: "config"
""")
    prev_env = {}
    for key in ("CONFIG_DIR", "TEST_OSS_BUCKET", "TEST_MINIO_BUCKET"):
        prev_env[key] = os.environ.pop(key, None)
    try:
        os.environ["CONFIG_DIR"] = str(tmp_path)
        os.environ["TEST_OSS_BUCKET"] = "aura-mem-test"
        os.environ["TEST_MINIO_BUCKET"] = "archives-test"
        reset_app_settings_cache()
        settings = get_app_settings()
        assert settings.oss_bucket == "aura-mem-test"
        assert settings.minio_bucket == "archives-test"
    finally:
        for k, v in prev_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        reset_app_settings_cache()


def test_get_app_settings_defaults_when_no_app_yaml(monkeypatch):
    """When app.yaml is missing, get_app_settings returns defaults (and resets global)."""
    import app.config.loader as loader
    loader._app_settings = None
    monkeypatch.setattr(loader, "_app_config_path", lambda: Path("/nonexistent/app.yaml"))
    settings = get_app_settings()
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert "postgresql" in settings.database_url
    assert "localhost" in settings.database_url or "127.0.0.1" in settings.database_url
    assert "5432" in settings.database_url
    assert settings.config_dir == "config"
    loader._app_settings = None


def test_get_app_settings_loads_from_yaml(tmp_path):
    """get_app_settings loads host, port, database_url from app.yaml."""
    (tmp_path / "app.yaml").write_text("""
host: "127.0.0.1"
port: 9999
database_url: "postgresql+asyncpg://u:p@host:5433/db"
config_dir: "config"
""")
    import app.config.loader as loader
    loader._app_settings = None
    try:
        os.environ["CONFIG_DIR"] = str(tmp_path)
        settings = get_app_settings()
        assert settings.host == "127.0.0.1"
        assert settings.port == 9999
        assert settings.database_url == "postgresql+asyncpg://u:p@host:5433/db"
    finally:
        os.environ.pop("CONFIG_DIR", None)
        loader._app_settings = None


def test_get_app_settings_dashscope_key_in_yaml(tmp_path):
    """When dashscope_api_key is set in app.yaml, it is applied to os.environ."""
    (tmp_path / "app.yaml").write_text("""
dashscope_api_key: "sk-test-from-yaml"
config_dir: "config"
""")
    import app.config.loader as loader
    loader._app_settings = None
    prev_key = os.environ.get("DASHSCOPE_API_KEY")
    try:
        os.environ.pop("DASHSCOPE_API_KEY", None)
        os.environ["CONFIG_DIR"] = str(tmp_path)
        settings = get_app_settings()
        assert settings.dashscope_api_key == "sk-test-from-yaml"
        assert os.environ.get("DASHSCOPE_API_KEY") == "sk-test-from-yaml"
    finally:
        if prev_key is not None:
            os.environ["DASHSCOPE_API_KEY"] = prev_key
        else:
            os.environ.pop("DASHSCOPE_API_KEY", None)
        os.environ.pop("CONFIG_DIR", None)
        loader._app_settings = None


def test_validate_required_env_passes_when_key_in_config(tmp_path):
    """validate_required_env does not raise when dashscope_api_key is set in app.yaml."""
    (tmp_path / "app.yaml").write_text("""
dashscope_api_key: "sk-in-config"
config_dir: "config"
required_env_vars: []
""")
    import app.config.loader as loader
    loader._app_settings = None
    prev = os.environ.get("DASHSCOPE_API_KEY"), os.environ.get("CONFIG_DIR")
    try:
        os.environ.pop("DASHSCOPE_API_KEY", None)
        os.environ["CONFIG_DIR"] = str(tmp_path)
        validate_required_env()
    finally:
        os.environ.pop("CONFIG_DIR", None)
        if prev[0] is not None:
            os.environ["DASHSCOPE_API_KEY"] = prev[0]
        loader._app_settings = None


def test_validate_required_env_raises_when_no_key(tmp_path):
    """validate_required_env raises when no dashscope key in config and env unset."""
    (tmp_path / "app.yaml").write_text("""
config_dir: "config"
required_env_vars: [DASHSCOPE_API_KEY]
""")
    import app.config.loader as loader
    loader._app_settings = None
    prev = os.environ.get("DASHSCOPE_API_KEY"), os.environ.get("CONFIG_DIR")
    try:
        os.environ.pop("DASHSCOPE_API_KEY", None)
        os.environ["CONFIG_DIR"] = str(tmp_path)
        with pytest.raises(SystemExit):
            validate_required_env()
    finally:
        if prev[0] is not None:
            os.environ["DASHSCOPE_API_KEY"] = prev[0]
        os.environ.pop("CONFIG_DIR", None)
        loader._app_settings = None


def test_local_start_export_database_url(tmp_path):
    """Simulate run script: loading app.yaml and getting DATABASE_URL for DB wait."""
    (tmp_path / "app.yaml").write_text("""
database_url: "postgresql+asyncpg://u:p@dbhost:5433/mydb"
config_dir: "config"
""")
    import app.config.loader as loader
    loader._app_settings = None
    try:
        os.environ["CONFIG_DIR"] = str(tmp_path)
        s = get_app_settings()
        db_url = s.database_url
        assert "dbhost" in db_url
        assert "5433" in db_url
        assert "mydb" in db_url
    finally:
        os.environ.pop("CONFIG_DIR", None)
        loader._app_settings = None


def test_serve_uses_app_config_host_port(tmp_path):
    """CLI serve uses host/port from app.yaml when --host/--port not passed."""
    (tmp_path / "app.yaml").write_text("""
host: "127.0.0.1"
port: 8765
config_dir: "config"
""")
    import app.config.loader as loader
    loader._app_settings = None
    try:
        os.environ["CONFIG_DIR"] = str(tmp_path)
        settings = get_app_settings()
        assert settings.host == "127.0.0.1"
        assert settings.port == 8765
    finally:
        os.environ.pop("CONFIG_DIR", None)
        loader._app_settings = None


def test_app_yaml_substitutes_env(tmp_path):
    """app.yaml values with ${VAR} are substituted from environment."""
    os.environ["MY_DB_HOST"] = "pg.example.com"
    (tmp_path / "app.yaml").write_text("""
database_url: "postgresql+asyncpg://u:p@${MY_DB_HOST}:5432/db"
config_dir: "config"
""")
    import app.config.loader as loader
    loader._app_settings = None
    try:
        os.environ["CONFIG_DIR"] = str(tmp_path)
        settings = get_app_settings()
        assert "pg.example.com" in settings.database_url
    finally:
        os.environ.pop("MY_DB_HOST", None)
        os.environ.pop("CONFIG_DIR", None)
        loader._app_settings = None
