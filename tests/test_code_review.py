"""
Tests for code review: gather_code_files, build_review_prompt, run_code_review, POST /code-review.

Mocks subprocess or run_code_review so no real Copilot/Claude CLI is required.
"""

import subprocess
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.code_review.runner import (
    build_review_prompt,
    build_review_prompt_from_diffs,
    gather_code_files,
    gather_diffs_from_uncommitted,
    run_code_review,
    validate_commits_for_review,
)



@pytest.fixture
def mock_db():
    async def noop_init_db():
        pass

    result_mock = MagicMock()
    result_mock.fetchall.return_value = []
    session_mock = MagicMock()
    session_mock.execute = MagicMock(return_value=result_mock)
    session_mock.commit = MagicMock(return_value=None)
    session_mock.rollback = MagicMock(return_value=None)

    def add_and_assign_id(obj):
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()

    session_mock.add = add_and_assign_id
    session_mock.flush = MagicMock(return_value=None)

    class Ctx:
        async def __aenter__(self):
            return session_mock

        async def __aexit__(self, *args):
            pass

    factory_mock = MagicMock()
    factory_mock.return_value = Ctx()

    with patch("app.storage.db.init_db", side_effect=noop_init_db), patch(
        "app.storage.db.get_session_factory", return_value=factory_mock
    ), patch("app.storage.db.session_scope", new=lambda: Ctx()):
        yield session_mock


@pytest.fixture
def client(mock_db):
    with patch("app.main.validate_required_env"):
        from app.main import app

        with TestClient(app) as c:
            yield c


def test_gather_code_files_single_file(tmp_path):
    root = tmp_path
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("print('hello')", encoding="utf-8")
    files = gather_code_files("app/main.py", root=root)
    assert len(files) == 1
    assert files[0][0] == Path("app/main.py")
    assert "hello" in files[0][1]


def test_gather_code_files_directory(tmp_path):
    root = tmp_path
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("a = 1", encoding="utf-8")
    (root / "src" / "b.py").write_text("b = 2", encoding="utf-8")
    (root / "src" / "readme.txt").write_text("readme", encoding="utf-8")
    files = gather_code_files("src", root=root)
    assert len(files) == 2
    paths = {f[0] for f in files}
    assert Path("src/a.py") in paths
    assert Path("src/b.py") in paths


def test_gather_code_files_path_outside_root(tmp_path):
    root = tmp_path
    with pytest.raises(ValueError, match="outside allowed root"):
        gather_code_files("../etc", root=root)


def test_gather_code_files_nonexistent(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        gather_code_files("nonexistent", root=tmp_path)


def test_build_review_prompt():
    files = [(Path("a.py"), "x = 1"), (Path("b.py"), "y = 2")]
    prompt = build_review_prompt(files)
    assert "Summary" in prompt
    assert "Issues" in prompt
    assert "Suggestions" in prompt
    assert "### File: a.py" in prompt
    assert "x = 1" in prompt
    assert "y = 2" in prompt


def test_run_code_review_invalid_provider():
    with pytest.raises(ValueError, match="provider must be"):
        run_code_review(".", provider="invalid")


def test_code_review_api_success(client, tmp_path):
    (tmp_path / "one.py").write_text("code here", encoding="utf-8")
    with patch("app.routers.code_review.run_code_review") as m:
        m.return_value = {
            "report": "## Summary\nGood.",
            "provider": "claude",
            "files_included": 1,
            "stderr": "",
        }
        with patch("app.routers.code_review._code_review_root", return_value=str(tmp_path)):
            r = client.post(
                "/code-review",
                json={"path": ".", "provider": "claude"},
            )
    assert r.status_code == 200
    data = r.json()
    assert data["provider"] == "claude"
    assert "Summary" in data["report"]
    assert data["files_included"] == 1


def test_code_review_api_copilot(client, tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.py").write_text("x = 1", encoding="utf-8")
    with patch("app.routers.code_review.run_code_review") as m:
        m.return_value = {"report": "OK", "provider": "copilot", "files_included": 1, "stderr": ""}
        with patch("app.routers.code_review._code_review_root", return_value=str(tmp_path)):
            r = client.post(
                "/code-review",
                json={"path": "app", "provider": "copilot"},
            )
    assert r.status_code == 200
    assert r.json()["provider"] == "copilot"


def test_code_review_api_invalid_path_400(client, tmp_path):
    with patch("app.routers.code_review._code_review_root", return_value=str(tmp_path)):
        r = client.post(
            "/code-review",
            json={"path": "../outside", "provider": "claude"},
        )
    assert r.status_code == 400
    assert "detail" in r.json()


def test_gather_code_files_respects_max_files(tmp_path):
    root = tmp_path
    (root / "src").mkdir()
    for i in range(10):
        (root / "src" / f"f{i}.py").write_text("x", encoding="utf-8")
    files = gather_code_files("src", root=root, max_files=3)
    assert len(files) == 3


def test_gather_code_files_respects_max_total_bytes(tmp_path):
    root = tmp_path
    (root / "big").mkdir()
    (root / "big" / "a.py").write_text("x" * 2000, encoding="utf-8")
    (root / "big" / "b.py").write_text("y" * 2000, encoding="utf-8")
    files = gather_code_files("big", root=root, max_total_bytes=2500)
    assert len(files) >= 1
    total = sum(len(c.encode("utf-8")) for _, c in files)
    assert total <= 2500 + 100  # allow small overshoot from truncation


def test_gather_code_files_custom_extensions(tmp_path):
    root = tmp_path
    (root / "data").mkdir()
    (root / "data" / "a.py").write_text("a", encoding="utf-8")
    (root / "data" / "b.txt").write_text("b", encoding="utf-8")
    files = gather_code_files("data", root=root, extensions={".py"})
    assert len(files) == 1
    assert files[0][0].name == "a.py"


def test_run_code_review_no_files_returns_message(tmp_path):
    (tmp_path / "empty").mkdir()
    result = run_code_review("empty", "claude", root=tmp_path)
    assert result["report"] == "No code files found under the given path."
    assert result["files_included"] == 0
    assert result["provider"] == "claude"


def test_run_code_review_with_mocked_subprocess(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("print(1)", encoding="utf-8")
    with patch("subprocess.run") as m:
        m.return_value = MagicMock(stdout="## Summary\nAll good.", stderr="", returncode=0)
        result = run_code_review("app", "claude", root=tmp_path)
    assert result["report"] == "## Summary\nAll good."
    assert result["provider"] == "claude"
    assert result["files_included"] == 1
    assert m.called
    args = m.call_args[0][0]
    assert "claude" in args
    assert "-p" in args


def test_run_code_review_copilot_env_set(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "one" / "x.py").write_text("x", encoding="utf-8")
    with patch("subprocess.run") as m:
        m.return_value = MagicMock(stdout="OK", stderr="", returncode=0)
        run_code_review("one", "copilot", root=tmp_path)
    assert m.called
    env = m.call_args[1].get("env") or {}
    assert env.get("COPILOT_ALLOW_ALL") == "1"


def test_build_review_prompt_from_diffs():
    diffs = [("commit abc", "diff --git a/x b/x\n--- a/x\n+++ b/x\n+new"), ("commit def", "diff --git a/y b/y\n--- a/y\n+++ b/y\n-old")]
    prompt = build_review_prompt_from_diffs(diffs)
    assert "commit abc" in prompt
    assert "commit def" in prompt
    assert "diff --git" in prompt
    assert "Git patches to review" in prompt or "Review the following" in prompt


def test_run_code_review_with_commits_not_git_repo_raises(tmp_path):
    """run_code_review(commits=[...]) in non-git dir raises ValueError."""
    (tmp_path / "sub").mkdir()
    with pytest.raises(ValueError, match="not a git repository"):
        run_code_review("app", "claude", root=tmp_path, commits=["abc123"])


def test_run_code_review_uncommitted_only_not_git_repo_raises(tmp_path):
    """run_code_review(uncommitted_only=True) in non-git dir raises ValueError."""
    with pytest.raises(ValueError, match="not a git repository"):
        run_code_review("app", "claude", root=tmp_path, uncommitted_only=True)


# --- validate_commits_for_review corner cases ---
def test_validate_commits_empty_list():
    valid, err = validate_commits_for_review([])
    assert valid is False
    assert "请填写至少一个 commit" in (err or "")


def test_validate_commits_whitespace_only():
    valid, err = validate_commits_for_review(["  ", "\n", ""])
    assert valid is False
    assert err


def test_validate_commits_not_git_repo(tmp_path):
    valid, err = validate_commits_for_review(["abc123"], root=tmp_path)
    assert valid is False
    assert "not a git repository" in (err or "")


def test_validate_commits_valid_with_mocked_git(tmp_path):
    """When git root and checks pass, returns (True, None)."""
    with patch("app.code_review.runner._git_root", return_value=tmp_path), \
         patch("app.code_review.runner._check_commits_in_tree"), \
         patch("app.code_review.runner._check_git_clean"):
        valid, err = validate_commits_for_review(["abc123"], root=tmp_path)
    assert valid is True
    assert err is None


# --- gather_diffs_from_uncommitted path safety ---
def test_gather_diffs_from_uncommitted_path_outside_root_ignored(tmp_path):
    """Path outside root does not get passed to git (safe)."""
    with patch("subprocess.run") as m:
        m.return_value = MagicMock(stdout="", stderr="", returncode=0)
        # path like ../ would be resolved; _is_safe_path would be False so we don't add -- path
        result = gather_diffs_from_uncommitted(tmp_path, path="../etc", max_total_bytes=1000)
    assert result == []
    call_args = m.call_args[0][0]
    assert "git" in call_args and "diff" in call_args
    # Should not include "-- ../etc" because not safe
    assert "../etc" not in call_args


def test_code_review_api_invalid_provider_400(client, tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.py").write_text("x", encoding="utf-8")
    with patch("app.routers.code_review._code_review_root", return_value=str(tmp_path)):
        r = client.post(
            "/code-review",
            json={"path": "app", "provider": "invalid"},
        )
    assert r.status_code == 400
    assert "detail" in r.json()


def test_code_review_api_timeout_504(client, tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.py").write_text("x", encoding="utf-8")
    with patch("app.routers.code_review.run_code_review") as m:
        m.side_effect = subprocess.TimeoutExpired("claude", 180)
        with patch("app.routers.code_review._code_review_root", return_value=str(tmp_path)):
            r = client.post(
                "/code-review",
                json={"path": "app", "provider": "claude"},
            )
    assert r.status_code == 504
    assert "timeout" in r.json().get("detail", "").lower()


def test_code_review_stream_api(client, tmp_path):
    """POST /code-review/stream returns SSE and yields log then report events."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "a.py").write_text("a = 1", encoding="utf-8")
    with patch("app.routers.code_review.run_code_review_stream") as mock_stream:
        def gen():
            yield {"type": "log", "message": "正在收集代码文件…"}
            yield {"type": "log", "message": "已找到 1 个文件。"}
            yield {"type": "report", "report": "## Summary\nOK", "provider": "claude", "files_included": 1, "stderr": ""}
        mock_stream.return_value = gen()
        with patch("app.routers.code_review._code_review_root", return_value=str(tmp_path)):
            r = client.post(
                "/code-review/stream",
                json={"path": "lib", "provider": "claude"},
            )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")
    text = r.text
    assert '"type": "log"' in text
    assert '"type": "report"' in text
    assert "report" in text and "Summary" in text


def test_code_review_api_accepts_optional_params(client, tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "a.py").write_text("a", encoding="utf-8")
    with patch("app.routers.code_review.run_code_review") as m:
        m.return_value = {"report": "R", "provider": "claude", "files_included": 1, "stderr": ""}
        with patch("app.routers.code_review._code_review_root", return_value=str(tmp_path)):
            r = client.post(
                "/code-review",
                json={
                    "path": "lib",
                    "provider": "claude",
                    "max_files": 50,
                    "max_total_bytes": 100000,
                    "timeout_seconds": 120,
                },
            )
    assert r.status_code == 200
    assert m.called
    call_kw = m.call_args[1]
    assert call_kw["max_files"] == 50
    assert call_kw["max_total_bytes"] == 100000
    assert call_kw["timeout_seconds"] == 120


def test_code_review_stream_with_commits(client, tmp_path):
    """POST /code-review/stream with commits passes commits to runner."""
    with patch("app.routers.code_review.run_code_review_stream") as mock_stream:
        def gen():
            yield {"type": "log", "message": "Git 检查通过…"}
            yield {"type": "report", "report": "## Summary\nOK", "provider": "claude", "files_included": 2, "stderr": ""}
        mock_stream.return_value = gen()
        with patch("app.routers.code_review._code_review_root", return_value=str(tmp_path)):
            r = client.post(
                "/code-review/stream",
                json={"path": "app", "provider": "claude", "commits": ["abc123", "def456"]},
            )
    assert r.status_code == 200
    assert mock_stream.called
    call_kw = mock_stream.call_args[1]
    assert call_kw["commits"] == ["abc123", "def456"]


def test_code_review_stream_uncommitted_only(client, tmp_path):
    """POST /code-review/stream with uncommitted_only passes flag to runner."""
    with patch("app.routers.code_review.run_code_review_stream") as mock_stream:
        def gen():
            yield {"type": "log", "message": "正在收集当前未提交的变更…"}
            yield {"type": "report", "report": "## Summary\nOK", "provider": "claude", "files_included": 1, "stderr": ""}
        mock_stream.return_value = gen()
        with patch("app.routers.code_review._code_review_root", return_value=str(tmp_path)):
            r = client.post(
                "/code-review/stream",
                json={"path": "app", "provider": "claude", "uncommitted_only": True},
            )
    assert r.status_code == 200
    assert mock_stream.called
    call_kw = mock_stream.call_args[1]
    assert call_kw["uncommitted_only"] is True


def test_code_review_stream_first_event_error(client, tmp_path):
    """When runner raises, stream yields error event and stops."""
    with patch("app.routers.code_review.run_code_review_stream") as mock_stream:
        mock_stream.side_effect = ValueError("not a git repository")
        with patch("app.routers.code_review._code_review_root", return_value=str(tmp_path)):
            r = client.post(
                "/code-review/stream",
                json={"path": "app", "provider": "claude"},
            )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")
    text = r.text
    assert '"type": "error"' in text
    assert "not a git repository" in text
