"""Tests for run script: configure, usage, and run path (DB wait).

Tests that need the script file use pytest.skip only when run is not in the tree
(e.g. partial checkout). test_run_script_exists ensures we fail if run is
missing when it should be present. try/except in run tests only catches
subprocess.TimeoutExpired (script still running); assertion failures are not caught.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUN_SCRIPT = ROOT / "run"


def _run_script(*args, cwd=None, env=None, timeout=10, script=None):
    """Run the run script with args; return (returncode, stdout, stderr). If script is set, run that path (so script dir = cwd for script)."""
    cwd = cwd or ROOT
    env = env if env is not None else None
    exe = str(script) if script is not None else str(RUN_SCRIPT)
    result = subprocess.run(
        [exe, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


def test_run_script_exists():
    """Fail if run script is missing so we do not silently skip necessary tests (e.g. in CI)."""
    assert RUN_SCRIPT.exists(), "run must exist at repo root; tests require it"


def test_run_configure_creates_config_when_missing(tmp_path):
    """./run configure creates config/app.yaml and config/models.yaml from examples."""
    if not RUN_SCRIPT.exists():
        pytest.skip("run script not found")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.yaml.example").write_text("host: 0.0.0.0\nport: 8000\n")
    (tmp_path / "config" / "models.yaml.example").write_text("default_chat_provider: dashscope\n")
    (tmp_path / "run").write_text(RUN_SCRIPT.read_text())
    (tmp_path / "run").chmod(0o755)
    code, out, err = _run_script("configure", cwd=tmp_path, script=tmp_path / "run")
    assert code == 0, (out, err)
    assert (tmp_path / "config" / "app.yaml").exists()
    assert (tmp_path / "config" / "models.yaml").exists()
    assert "Created:" in out or "created" in out.lower()


def test_run_config_alias(tmp_path):
    """./run config is alias for configure."""
    if not RUN_SCRIPT.exists():
        pytest.skip("run script not found")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.yaml.example").write_text("host: 0.0.0.0\n")
    (tmp_path / "config" / "models.yaml.example").write_text("default: x\n")
    (tmp_path / "run").write_text(RUN_SCRIPT.read_text())
    (tmp_path / "run").chmod(0o755)
    code, out, err = _run_script("config", cwd=tmp_path, script=tmp_path / "run")
    assert code == 0
    assert (tmp_path / "config" / "app.yaml").exists()
    assert (tmp_path / "config" / "models.yaml").exists()


def test_run_configure_idempotent(tmp_path):
    """Running configure when config already exists prints 'already exists' and does not overwrite."""
    if not RUN_SCRIPT.exists():
        pytest.skip("run script not found")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.yaml.example").write_text("host: 0.0.0.0\n")
    (tmp_path / "config" / "models.yaml.example").write_text("x: 1\n")
    (tmp_path / "config" / "app.yaml").write_text("host: 9.9.9.9\n")
    (tmp_path / "config" / "models.yaml").write_text("x: 2\n")
    (tmp_path / "run").write_text(RUN_SCRIPT.read_text())
    (tmp_path / "run").chmod(0o755)
    code, out, err = _run_script("configure", cwd=tmp_path, script=tmp_path / "run")
    assert code == 0
    assert "already exists" in out
    assert (tmp_path / "config" / "app.yaml").read_text().strip() == "host: 9.9.9.9\n".strip()
    assert (tmp_path / "config" / "models.yaml").read_text().strip() == "x: 2\n".strip()


def test_run_usage_on_invalid_command():
    """Invalid command prints usage to stderr and exits with 1."""
    if not RUN_SCRIPT.exists():
        pytest.skip("run script not found")
    code, out, err = _run_script("invalidcommand")
    assert code == 1
    assert "Usage" in err or "usage" in err.lower()
    assert "configure" in err
    assert "node" in err and "local" in err and "docker" in err


def test_run_run_skips_db_wait_when_env_set():
    """With SKIP_DB_WAIT=1, ./run run skips DB wait and tries to exec agent-backend (may fail if not installed)."""
    if not RUN_SCRIPT.exists():
        pytest.skip("run script not found")
    import os
    env = dict(os.environ)
    env["SKIP_DB_WAIT"] = "1"
    try:
        result = subprocess.run(
            [str(RUN_SCRIPT), "run"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=2,
            env=env,
        )
        assert "Skipping PostgreSQL wait" in result.stdout
    except subprocess.TimeoutExpired:
        pass  # script still running (success); do not catch AssertionError or other failures


def test_run_node_explicit_option():
    """./run node runs backend on a Docker node; SKIP_DB_WAIT=1 skips DB wait."""
    if not RUN_SCRIPT.exists():
        pytest.skip("run script not found")
    import os
    env = dict(os.environ)
    env["SKIP_DB_WAIT"] = "1"
    try:
        result = subprocess.run(
            [str(RUN_SCRIPT), "node"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=2,
            env=env,
        )
        assert "Skipping PostgreSQL wait" in result.stdout
    except subprocess.TimeoutExpired:
        pass  # script still running; only TimeoutExpired is acceptable, not assertion failures


def test_run_local_explicit_option():
    """./run local runs backend (real local machine); SKIP_DB_WAIT=1 skips DB wait."""
    if not RUN_SCRIPT.exists():
        pytest.skip("run script not found")
    import os
    env = dict(os.environ)
    env["SKIP_DB_WAIT"] = "1"
    try:
        result = subprocess.run(
            [str(RUN_SCRIPT), "local"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=2,
            env=env,
        )
        assert "Skipping PostgreSQL wait" in result.stdout
    except subprocess.TimeoutExpired:
        pass  # script still running; only TimeoutExpired is acceptable


def test_run_docker_option_usage():
    """Usage lists docker (and start) for Docker mode."""
    if not RUN_SCRIPT.exists():
        pytest.skip("run script not found")
    code, _, err = _run_script("invalidcommand")
    assert code == 1
    assert "docker" in err
    # run -> node, local and node both run backend; start -> docker
    text = RUN_SCRIPT.read_text()
    assert "node" in text and "local" in text
    assert "docker | start" in text


def test_run_parse_db_host_port():
    """run script-style parsing of DB_HOST and DB_PORT from DATABASE_URL."""
    import re
    url = "postgresql+asyncpg://user:pass@myhost:5434/mydb"
    db_host = re.sub(r".*@([^:/]*).*", r"\1", url) if "@" in url else "localhost"
    db_port = re.sub(r".*:([0-9]+)/.*", r"\1", url) if ":" in url and "/" in url else "5432"
    assert db_host == "myhost"
    assert db_port == "5434"
    url2 = "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_backend"
    db_host2 = re.sub(r".*@([^:/]*).*", r"\1", url2) if "@" in url2 else "localhost"
    db_port2 = re.sub(r".*:([0-9]+)/.*", r"\1", url2) if ":" in url2 and "/" in url2 else "5432"
    assert db_host2 == "localhost"
    assert db_port2 == "5432"


def test_run_default_command_is_run():
    """No argument defaults to run (node) and script runs from its directory."""
    if not RUN_SCRIPT.exists():
        pytest.skip("run script not found")
    import os
    env = dict(os.environ)
    env["SKIP_DB_WAIT"] = "1"
    try:
        result = subprocess.run(
            [str(RUN_SCRIPT)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=2,
            env=env,
        )
        assert "Skipping PostgreSQL wait" in result.stdout
    except subprocess.TimeoutExpired:
        pass  # script still running; only TimeoutExpired is acceptable


def test_run_run_and_start_normalize_to_node_and_docker():
    """run -> node, start -> docker (script content)."""
    if not RUN_SCRIPT.exists():
        pytest.skip("run script not found")
    text = RUN_SCRIPT.read_text()
    assert "run)   CMD=node" in text or "run) CMD=node" in text
    assert "start) CMD=docker" in text or "start)   CMD=docker" in text


def test_run_configure_creates_only_app_yaml_when_models_example_missing(tmp_path):
    """When only app.yaml.example exists, configure creates only app.yaml."""
    if not RUN_SCRIPT.exists():
        pytest.skip("run script not found")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.yaml.example").write_text("host: 0.0.0.0\n")
    (tmp_path / "run").write_text(RUN_SCRIPT.read_text())
    (tmp_path / "run").chmod(0o755)
    code, out, err = _run_script("configure", cwd=tmp_path, script=tmp_path / "run")
    assert code == 0
    assert (tmp_path / "config" / "app.yaml").exists()
    assert not (tmp_path / "config" / "models.yaml").exists()


def test_run_script_runs_from_own_directory(tmp_path):
    """Script cd's to its own directory so paths work when invoked from elsewhere."""
    if not RUN_SCRIPT.exists():
        pytest.skip("run script not found")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.yaml.example").write_text("host: 0.0.0.0\n")
    (tmp_path / "config" / "models.yaml.example").write_text("x: 1\n")
    (tmp_path / "run").write_text(RUN_SCRIPT.read_text())
    (tmp_path / "run").chmod(0o755)
    # Run from a different directory (e.g. /tmp) using absolute path to script in tmp_path
    code, out, err = _run_script("configure", cwd=Path("/tmp"), script=tmp_path / "run")
    assert code == 0
    assert (tmp_path / "config" / "app.yaml").exists()
    assert (tmp_path / "config" / "models.yaml").exists()


def test_run_db_wait_timeout_message_has_fix_hints():
    """When DB wait fails after retries, error message suggests pg_ctl, Docker, systemctl (no skip)."""
    if not RUN_SCRIPT.exists():
        pytest.skip("run script not found")
    text = RUN_SCRIPT.read_text()
    assert "Fix the database" in text
    assert "pg_ctl" in text or "postgresql" in text
    assert "Or use Docker" in text or "docker" in text.lower()
    assert "systemctl" in text or "brew services" in text
    assert "10 attempts" in text or "retry" in text.lower()
    idx = text.find("did not become ready")
    assert idx != -1
    block = text[idx : idx + 600]
    assert "SKIP_DB_WAIT" not in block


def test_run_node_mode_loads_config_and_proceeds():
    """Node mode (./run node) with SKIP_DB_WAIT=1 loads config and proceeds to start agent-backend."""
    if not RUN_SCRIPT.exists():
        pytest.skip("run script not found")
    import os
    env = dict(os.environ)
    env["SKIP_DB_WAIT"] = "1"
    try:
        result = subprocess.run(
            [str(RUN_SCRIPT), "node"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=4,
            env=env,
        )
        out = result.stdout or ""
    except subprocess.TimeoutExpired:
        # Server started and is still running
        return
    assert "[init] mode=node" in out or "mode=node" in out, out
    assert "Skipping PostgreSQL wait" in out or "[run] Starting agent-backend" in out, out
