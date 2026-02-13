"""
Config loader: YAML loading, env variable injection, Pydantic validation, hot reload.

- App settings from config/app.yaml (host, port, database_url, dashscope_api_key, etc.).
- Models config from config/models.yaml (embedding/chat providers, summary strategies).
- Hot reload: watchdog monitors config directory and reloads on change.
- Environment variable injection: ${ENV_VAR} replacement in YAML values.
- Startup: validates required API key (in app.yaml or env) and exits with clear error if missing.
"""

import importlib.util
import os
import re
from pathlib import Path
from typing import Any, Callable

import yaml

from app.config.schemas import AppSettings, ModelsConfig

# Global config instance (reloadable)
_models_config: ModelsConfig | None = None
_app_settings: AppSettings | None = None


def reset_app_settings_cache() -> None:
    """Clear cached app settings (for tests). Next get_app_settings() will reload from config and env."""
    global _app_settings
    _app_settings = None


def _substitute_env(value: Any) -> Any:
    """Replace ${VAR} and $VAR in strings; recurse into dict/list."""
    if isinstance(value, str):
        # Match ${VAR} or $VAR
        pattern = re.compile(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
        def repl(m: re.Match[str]) -> str:
            name = m.group(1) or m.group(2) or ""
            return os.environ.get(name, m.group(0))
        return pattern.sub(repl, value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML file and return dict with env substitution."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    return _substitute_env(data)


def validate_required_env(required: list[str] | None = None) -> None:
    """
    Check that required environment variables are set (or API key is in app config).
    Exits with code 1 and prints missing vars if any.
    """
    settings = get_app_settings()
    if settings.dashscope_api_key and settings.dashscope_api_key.strip():
        return
    names = required or settings.required_env_vars
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        msg = f"Missing required settings: set dashscope_api_key in config/app.yaml or set env: {', '.join(missing)}."
        raise SystemExit(msg)


def _app_config_path() -> Path:
    """Path to app.yaml; CONFIG_DIR env or default 'config'."""
    base = Path(os.environ.get("CONFIG_DIR", "config")).resolve()
    return base / "app.yaml"


def get_app_settings() -> AppSettings:
    """Return application settings (from config/app.yaml); load once and apply API key to env.
    When running tests, set TEST_DATABASE_URL / TEST_OSS_BUCKET / TEST_MINIO_BUCKET to avoid
    polluting dev/prod database and buckets.
    """
    global _app_settings
    if _app_settings is None:
        path = _app_config_path()
        if path.exists():
            data = _load_yaml(path)
            if os.environ.get("DATABASE_URL"):
                data["database_url"] = os.environ["DATABASE_URL"]
            if os.environ.get("TEST_DATABASE_URL"):
                data["database_url"] = os.environ["TEST_DATABASE_URL"]
            if os.environ.get("TEST_OSS_BUCKET"):
                data["oss_bucket"] = os.environ["TEST_OSS_BUCKET"]
            if os.environ.get("TEST_MINIO_BUCKET"):
                data["minio_bucket"] = os.environ["TEST_MINIO_BUCKET"]
            _app_settings = AppSettings.model_validate(data)
        else:
            _app_settings = AppSettings()
        if _app_settings.dashscope_api_key and _app_settings.dashscope_api_key.strip():
            os.environ["DASHSCOPE_API_KEY"] = _app_settings.dashscope_api_key.strip()
    return _app_settings


def load_models_config(config_dir: str | None = None) -> ModelsConfig:
    """
    Load and validate models.yaml from config directory.

    Args:
        config_dir: Override config directory (default from AppSettings).

    Returns:
        Validated ModelsConfig.

    Raises:
        ValidationError: If YAML is invalid or does not match schema.
        FileNotFoundError: If config dir does not exist (when strict).
    """
    settings = get_app_settings()
    base = Path(config_dir or settings.config_dir)
    path = base / "models.yaml"
    if not path.exists():
        path = base / "models.yaml.example"
    data = _load_yaml(path)
    return ModelsConfig.model_validate(data)


def get_config() -> ModelsConfig:
    """Return current models config (loads on first call, then uses cached or reloaded)."""
    global _models_config
    if _models_config is None:
        _models_config = load_models_config()
    return _models_config


def reload_config(config_dir: str | None = None) -> ModelsConfig:
    """
    Force reload models config from disk (e.g. after admin trigger or file change).

    Args:
        config_dir: Optional override for config directory.

    Returns:
        Newly loaded ModelsConfig.

    Raises:
        ValidationError: If new config is invalid (previous config remains in effect).
    """
    global _models_config
    new_config = load_models_config(config_dir=config_dir)
    _models_config = new_config
    return _models_config


def start_config_watcher(callback: Callable[[str, str], None]) -> None:
    """
    Start watchdog observer on config directory; call callback when files change.

    Callback will be invoked with (event_type, path). Call reload_config() inside
    and handle ValidationError to avoid applying invalid config.
    """
    if importlib.util.find_spec("watchdog") is None:
        return
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    settings = get_app_settings()
    base = Path(settings.config_dir).resolve()
    if not base.exists():
        return

    class Handler(FileSystemEventHandler):
        def on_modified(self, event):
            if event.is_directory:
                return
            if event.src_path.endswith((".yaml", ".yml")):
                callback("modified", event.src_path)

    observer = Observer()
    observer.schedule(Handler(), str(base), recursive=False)
    observer.start()
