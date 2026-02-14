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
import shutil
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
        anth_key = getattr(_app_settings, "anthropic_api_key", None)
        anth_base = getattr(_app_settings, "anthropic_base_url", None)
        if anth_key and anth_key.strip():
            os.environ["ANTHROPIC_API_KEY"] = anth_key.strip()
        if anth_base and anth_base.strip():
            os.environ["ANTHROPIC_BASE_URL"] = anth_base.strip()
        elif anth_key and anth_key.strip():
            os.environ.setdefault("ANTHROPIC_BASE_URL", "https://gaccode.com/claudecode")
    return _app_settings


def _resolve_abilities_file_path(config_dir: str | None) -> Path | None:
    """
    Resolve path to ability repo file (Aura config/abilities.yaml).
    Uses AURA_ABILITIES_FILE env if set; else infers from config_dir when it looks like Aura generated (.aura).
    """
    path_str = os.environ.get("AURA_ABILITIES_FILE")
    if path_str:
        p = Path(path_str)
        return p if p.is_absolute() else p.resolve()
    if config_dir:
        base = Path(config_dir).resolve()
        # CONFIG_DIR from Aura is e.g. .../Aura-Swarm/.aura/generated_config -> aura_root = .../Aura-Swarm
        walk = base
        while walk != walk.parent:
            if walk.name == ".aura":
                return walk.parent / "config" / "abilities.yaml"
            walk = walk.parent
    return None


def _parse_abilities_yaml(raw: str) -> list[dict[str, Any]]:
    """Parse abilities from YAML: root list, or root dict with 'abilities' / 'local_tools' key."""
    data = yaml.safe_load(raw)
    if data is None:
        return []
    if isinstance(data, list):
        return [a for a in data if isinstance(a, dict) and a.get("id")]
    if isinstance(data, dict):
        for key in ("abilities", "local_tools"):
            val = data.get(key)
            if isinstance(val, list):
                return [a for a in val if isinstance(a, dict) and a.get("id")]
    return []


def _merge_aura_abilities_into_data(data: dict[str, Any], config_dir: str | None = None) -> None:
    """
    Load ability repo (Aura config/abilities.yaml) and merge into data["local_tools"] by id.
    Enables web to see abilities from ability repo; path from AURA_ABILITIES_FILE or inferred from CONFIG_DIR.
    """
    ab_path = _resolve_abilities_file_path(config_dir)
    if ab_path is None or not ab_path.exists():
        return
    raw = ab_path.read_text(encoding="utf-8")
    ab_list = _parse_abilities_yaml(raw)
    if not ab_list:
        return
    base_tools = data.get("local_tools") or []
    by_id: dict[str, Any] = {}
    for t in base_tools:
        if isinstance(t, dict) and t.get("id"):
            by_id[t["id"]] = t
    for a in ab_list:
        if isinstance(a, dict) and a.get("id"):
            by_id[a["id"]] = a
    data["local_tools"] = list(by_id.values())


def _sync_models_yaml_from_backend_source(config_dir: str | Path) -> None:
    """
    When BACKEND_CONFIG_SOURCE is set (e.g. by Aura), copy models.yaml from that
    directory into config_dir so 刷新配置 picks up the latest backend models (e.g. cursor-local, copilot-local).
    """
    source_dir = os.environ.get("BACKEND_CONFIG_SOURCE")
    if not source_dir:
        return
    src = Path(source_dir).resolve()
    if not src.is_dir():
        return
    dst = Path(config_dir).resolve()
    if src == dst:
        return
    for name in ("models.yaml", "models.yaml.example"):
        src_file = src / name
        if src_file.exists():
            shutil.copy2(src_file, dst / name)
            break


def load_models_config(config_dir: str | None = None) -> ModelsConfig:
    """
    Load and validate models.yaml from config directory.
    When AURA_ABILITIES_FILE is set, merges that file's abilities into local_tools (by id)
    so that Aura 仓库中新增能力 can be recognized after reload without restart.
    When BACKEND_CONFIG_SOURCE is set (e.g. by Aura), syncs models.yaml from that path first
    so 刷新配置 shows the latest backend models (cursor-local, copilot-local, etc.).

    Args:
        config_dir: Override config directory (default from AppSettings).

    Returns:
        Validated ModelsConfig.
    """
    settings = get_app_settings()
    base = Path(config_dir or settings.config_dir)
    _sync_models_yaml_from_backend_source(base)
    path = base / "models.yaml"
    if not path.exists():
        path = base / "models.yaml.example"
    data = _load_yaml(path)
    _merge_aura_abilities_into_data(data, config_dir=(config_dir or getattr(settings, "config_dir", None)))
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


def update_default_chat_model(model_id: str, config_dir: str | None = None) -> str:
    """
    Set the default chat model in models.yaml and reload config.
    model_id 可属于任意已配置的 chat_provider（如 dashscope 或 anthropic），
    会更新该 provider 的 model 字段。

    Args:
        model_id: Model ID (must be in some provider's models list).
        config_dir: Optional override for config directory.

    Returns:
        The new default model id.

    Raises:
        ValueError: If models.yaml missing or model_id not in any provider.
    """
    settings = get_app_settings()
    base = Path(config_dir or settings.config_dir).resolve()
    path = base / "models.yaml"
    if not path.exists():
        raise ValueError("config/models.yaml not found; cannot update default model")
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    providers = data.get("chat_providers") or {}
    found_provider = None
    for name, prov in providers.items():
        allowed = prov.get("models") or [prov.get("model", "")]
        if model_id in allowed:
            found_provider = name
            prov["model"] = model_id
            break
    if found_provider is None:
        all_models = []
        for prov in providers.values():
            all_models.extend(prov.get("models") or [prov.get("model", "")] or [])
        raise ValueError(f"model {model_id!r} must be one of: {list(dict.fromkeys(all_models))}")
    path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    reload_config(config_dir=config_dir or settings.config_dir)
    return model_id


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
