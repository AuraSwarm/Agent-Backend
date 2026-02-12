"""Tests for the main CLI entry point (agent-backend = app.cli:main)."""

import subprocess
import sys
from unittest.mock import patch

import pytest

from app import __version__
from app.cli import main


def test_main_version_exits_zero_and_prints_version(capsys):
    """Main entry with 'version' subcommand exits 0 and prints version."""
    with patch.object(sys, "argv", ["agent-backend", "version"]):
        exit_code = main()
    assert exit_code == 0
    out, err = capsys.readouterr()
    assert __version__ in out or __version__ in err
    assert __version__.strip() in (out + err)


def test_main_help_exits_zero_and_lists_serve(capsys):
    """Main entry with --help exits 0 (via SystemExit) and shows serve and version."""
    with patch.object(sys, "argv", ["agent-backend", "--help"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 0
    out, err = capsys.readouterr()
    combined = out + err
    assert "serve" in combined
    assert "version" in combined


def test_main_serve_help_exits_zero(capsys):
    """Main entry with 'serve --help' exits 0 (via SystemExit) and shows host/port/reload."""
    with patch.object(sys, "argv", ["agent-backend", "serve", "--help"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 0
    out, err = capsys.readouterr()
    combined = out + err
    assert "serve" in combined or "host" in combined
    assert "--port" in combined or "port" in combined


def test_module_main_version_via_subprocess():
    """Running python -m app.cli version exits 0 and prints version (tests __main__ path)."""
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "version"],
        cwd=None,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert __version__ in (result.stdout + result.stderr)
