"""
Single entry point for all agent-backend usage: serve, test, init-db, reload-config, archive, health, version.
"""

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from app import __version__


def _env_path() -> Path:
    """Path to shell script for API keys; from config (ai_env_path) or env AI_ENV_PATH or ~/.ai_env.sh. Empty in config = disabled."""
    from app.config.loader import get_app_settings
    settings = get_app_settings()
    if settings.ai_env_path is not None:
        if settings.ai_env_path == "":
            return Path("/nonexistent")  # Disable loading
        return Path(settings.ai_env_path).expanduser()
    return Path(os.environ.get("AI_ENV_PATH", os.path.expanduser("~/.ai_env.sh")))


def _load_ai_env() -> bool:
    """Load ai_env_path script into os.environ; set DASHSCOPE_API_KEY from QWEN_API_KEY if needed."""
    p = _env_path()
    if not p.exists():
        return False
    import re
    pattern = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
    for line in p.read_text().splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        name, value = m.group(1), m.group(2).strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        os.environ[name] = value
    if os.environ.get("QWEN_API_KEY") and not os.environ.get("DASHSCOPE_API_KEY"):
        os.environ["DASHSCOPE_API_KEY"] = os.environ["QWEN_API_KEY"]
    return True


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the FastAPI server (Uvicorn). Host/port from config/app.yaml or args."""
    from app.config.loader import get_app_settings
    settings = get_app_settings()
    host = args.host if args.host is not None else settings.host
    port = args.port if args.port is not None else settings.port
    import errno
    import uvicorn
    try:
        uvicorn.run(
            "app.main:app",
            host=host,
            port=port,
            reload=args.reload,
        )
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            print(f"Port {port} is already in use. Stop the existing server first: Aura down", file=sys.stderr)
            print(f"Or use another port: Aura serve --port 8001", file=sys.stderr)
        raise
    return 0


def cmd_init_db(_: argparse.Namespace) -> int:
    """Create database extension and tables."""
    from app.storage.db import init_db
    asyncio.run(init_db())
    print("DB and tables ready.")
    return 0


def _backend_root() -> Path:
    """Agent-Backend repo root (parent of app/)."""
    return Path(__file__).resolve().parent.parent


def cmd_test(args: argparse.Namespace) -> int:
    """Run pytest in Agent-Backend repo. With --real-api, load ~/.ai_env.sh and set RUN_REAL_AI_TESTS=1."""
    if args.real_api:
        _load_ai_env()
        os.environ["RUN_REAL_AI_TESTS"] = "1"
    root = _backend_root()
    cmd = [sys.executable, "-m", "pytest", "tests/", "-v"]
    if args.cov:
        cmd += ["--cov=app", "--cov-report=term-missing"]
    extra = getattr(args, "pytest_args", None) or []
    cmd += [a for a in extra if a != "--"]
    if args.real_api and not _env_path().exists():
        print("Warning: --real-api set but", _env_path(), "not found; set DASHSCOPE_API_KEY or create the file.", file=sys.stderr)
    return subprocess.run(cmd, cwd=root).returncode


def cmd_reload_config(args: argparse.Namespace) -> int:
    """Trigger config hot reload (POST /admin/reload)."""
    import urllib.request
    url = f"{args.base_url.rstrip('/')}/admin/reload"
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        print(r.read().decode())
    return 0


def cmd_archive(_: argparse.Namespace) -> int:
    """Run the Celery archive task once."""
    return subprocess.run([
        sys.executable, "-m", "celery", "-A", "app.tasks.celery_app",
        "call", "app.tasks.archive_tasks.archive_by_activity",
    ]).returncode


def cmd_health(args: argparse.Namespace) -> int:
    """Check /health (readiness)."""
    import urllib.request
    url = f"{args.base_url.rstrip('/')}/health"
    with urllib.request.urlopen(url, timeout=5) as r:
        data = r.read().decode()
        print(data)
        return 0 if r.status == 200 else 1


def cmd_version(_: argparse.Namespace) -> int:
    """Print version."""
    print(__version__)
    return 0


def cmd_configure(_: argparse.Namespace) -> int:
    """Create config/app.yaml and config/models.yaml from examples if missing."""
    root = Path(__file__).resolve().parent.parent
    config_dir = root / "config"
    app_example = config_dir / "app.yaml.example"
    app_file = config_dir / "app.yaml"
    models_example = config_dir / "models.yaml.example"
    models_file = config_dir / "models.yaml"
    created = []
    config_dir.mkdir(parents=True, exist_ok=True)
    if not app_file.exists() and app_example.exists():
        app_file.write_text(app_example.read_text())
        created.append("config/app.yaml")
    elif app_file.exists():
        print("config/app.yaml already exists.")
    else:
        print("Warning: config/app.yaml.example not found.", file=sys.stderr)
    if not models_file.exists() and models_example.exists():
        models_file.write_text(models_example.read_text())
        created.append("config/models.yaml")
    elif models_file.exists():
        print("config/models.yaml already exists.")
    else:
        print("Warning: config/models.yaml.example not found.", file=sys.stderr)
    if created:
        print(f"Created: {', '.join(created)}. Edit config/app.yaml and set your values (e.g. dashscope_api_key, port).")
    return 0


def _compose_cmd() -> list[str]:
    """Return docker-compose or docker compose command prefix."""
    r = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True,
        check=False,
    )
    if r.returncode == 0:
        return ["docker", "compose"]
    return ["docker-compose"]


def cmd_start(args: argparse.Namespace) -> int:
    """Start Docker service(s). Default: all. Use --service to target one (e.g. backend)."""
    cmd = _compose_cmd() + ["up", "-d"]
    if args.service:
        cmd.extend(args.service)
    return subprocess.run(cmd).returncode


def cmd_stop(args: argparse.Namespace) -> int:
    """Stop Docker service(s). Default: all. Use --service to target one (e.g. backend)."""
    cmd = _compose_cmd() + ["stop"]
    if args.service:
        cmd.extend(args.service)
    return subprocess.run(cmd).returncode


def cmd_restart(args: argparse.Namespace) -> int:
    """Restart Docker service(s). Default: all. Use --service to target one (e.g. backend)."""
    cmd = _compose_cmd() + ["restart"]
    if args.service:
        cmd.extend(args.service)
    return subprocess.run(cmd).returncode


async def _try_one_model(provider_name: str, prov: Any, model: str, prompt: str) -> tuple[str, bool, str]:
    """Call one model; return (model, ok, message). Uses explicit check for timeout/response."""
    from app.adapters.cloud import CloudAPIAdapter
    adapter = CloudAPIAdapter(
        api_key_env=prov.api_key_env,
        endpoint=prov.endpoint,
        model=model,
        timeout=getattr(prov, "timeout", 60),
    )
    result = await adapter.call(prompt, model=model)
    out = result[0] if isinstance(result, tuple) else result
    msg = (out or "").strip()[:80]
    return (model, True, msg or "(empty)")


def cmd_try_models(args: argparse.Namespace) -> int:
    """Call each configured chat model with a short prompt; report OK or error (for debugging)."""
    _load_ai_env()
    from app.config.loader import get_config
    config = get_config()
    providers = getattr(config, "chat_providers", {}) or {}
    default_name = config.default_chat_provider or "dashscope"
    if default_name not in providers:
        print(f"No chat provider '{default_name}' in config.", file=sys.stderr)
        return 1
    prov = providers[default_name]
    models = getattr(prov, "models", None) or [prov.model]
    prompt = args.prompt or "Say OK in one word."
    print(f"Trying {len(models)} model(s) with prompt: {prompt!r}\n")
    failed = 0
    for model in models:
        res = asyncio.run(_try_one_model(default_name, prov, model, prompt))
        model_id, ok, msg = res
        if ok:
            print(f"  {model_id}: OK — {msg}")
        else:
            print(f"  {model_id}: FAIL — {msg}")
            failed += 1
    print(f"\nDone: {len(models) - failed}/{len(models)} OK")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="agent-backend",
        description="Agent Backend: serve, start/stop/restart (Docker), test, init-db, reload-config, archive, health, version.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # serve (host/port from config/app.yaml or --host/--port)
    p_serve = sub.add_parser("serve", help="Start the API server (Uvicorn)")
    p_serve.add_argument("--host", default=None, help="Bind host (default: from config/app.yaml)")
    p_serve.add_argument("--port", type=int, default=None, help="Bind port (default: from config/app.yaml)")
    p_serve.add_argument("--reload", action="store_true", help="Enable auto-reload (dev)")
    p_serve.set_defaults(func=cmd_serve)

    # init-db
    p_init = sub.add_parser("init-db", help="Create DB extension and tables")
    p_init.set_defaults(func=cmd_init_db)

    # test
    p_test = sub.add_parser("test", help="Run tests (pytest)")
    p_test.add_argument("--real-api", action="store_true", help="Load ~/.ai_env.sh and run real API integration tests")
    p_test.add_argument("--cov", action="store_true", help="Report coverage")
    p_test.add_argument("pytest_args", nargs=argparse.REMAINDER, default=[], metavar="[pytest-args...]", help="Extra args passed to pytest (e.g. -v, -x, tests/file.py)")
    p_test.set_defaults(func=cmd_test)

    # reload-config
    p_reload = sub.add_parser("reload-config", help="POST /admin/reload (hot reload config)")
    p_reload.add_argument("--base-url", default="http://localhost:8000", help="API base URL")
    p_reload.set_defaults(func=cmd_reload_config)

    # archive
    p_archive = sub.add_parser("archive", help="Run Celery archive task once")
    p_archive.set_defaults(func=cmd_archive)

    # health
    p_health = sub.add_parser("health", help="GET /health (readiness check)")
    p_health.add_argument("--base-url", default="http://localhost:8000", help="API base URL")
    p_health.set_defaults(func=cmd_health)

    # version
    p_version = sub.add_parser("version", help="Print version")
    p_version.set_defaults(func=cmd_version)

    # start (Docker)
    p_start = sub.add_parser("start", help="Start Docker service(s) (docker-compose up -d)")
    p_start.add_argument("service", nargs="*", help="Service name(s), e.g. backend (default: all)")
    p_start.set_defaults(func=cmd_start)

    # stop (Docker)
    p_stop = sub.add_parser("stop", help="Stop Docker service(s) (docker-compose stop)")
    p_stop.add_argument("service", nargs="*", help="Service name(s), e.g. backend (default: all)")
    p_stop.set_defaults(func=cmd_stop)

    # restart (Docker)
    p_restart = sub.add_parser("restart", help="Restart Docker service(s) (docker-compose restart)")
    p_restart.add_argument("service", nargs="*", help="Service name(s), e.g. backend (default: all)")
    p_restart.set_defaults(func=cmd_restart)

    # try-models
    p_try = sub.add_parser("try-models", help="Call each configured chat model with a short prompt; report OK or error")
    p_try.add_argument("--prompt", default="Say OK in one word.", help="Prompt to send (default: Say OK in one word.)")
    p_try.set_defaults(func=cmd_try_models)

    # configure
    p_configure = sub.add_parser("configure", help="Create config/app.yaml and config/models.yaml from examples if missing")
    p_configure.set_defaults(func=cmd_configure)

    args, unknown = parser.parse_known_args()
    if args.command == "test":
        args.pytest_args = getattr(args, "pytest_args", []) + unknown
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
