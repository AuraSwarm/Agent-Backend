"""
Tests for web UI behavior: stream stats, copy buttons, deep thinking.

Ensures static/app.js and index.html keep the contract:
- Streamed assistant messages show stats (总耗时) and copy/Markdown buttons.
- Stats line is always shown after stream ends (ensureStatsVisible).
- Page has messages area, deep thinking checkbox, and copy-related UI.

Run: pytest tests/test_web_ui.py -v
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
APP_JS = STATIC_DIR / "app.js"
INDEX_HTML = STATIC_DIR / "index.html"


def _read_app_js() -> str:
    assert APP_JS.exists(), f"Missing {APP_JS}"
    return APP_JS.read_text(encoding="utf-8")


def _read_index() -> str:
    assert INDEX_HTML.exists(), f"Missing {INDEX_HTML}"
    return INDEX_HTML.read_text(encoding="utf-8")


class TestStreamStatsInAppJs:
    """app.js must implement stream stats so 总耗时 is always shown."""

    def test_has_msg_stats_class(self):
        js = _read_app_js()
        assert "msg-stats" in js, "app.js must create/use .msg-stats for stats line"

    def test_has_total_duration_text(self):
        js = _read_app_js()
        assert "总耗时" in js, "app.js must display 总耗时 (total duration) in stats"

    def test_ensures_stats_visible_after_stream(self):
        js = _read_app_js()
        assert "ensureStatsVisible" in js or ("streamStartMs" in js and "耗时" in js), (
            "app.js must ensure stats line is visible after stream (ensureStatsVisible or equivalent)"
        )

    def test_processes_sse_usage_or_duration(self):
        js = _read_app_js()
        assert "duration_ms" in js and "usage" in js, (
            "app.js must handle SSE events with usage/duration_ms"
        )


class TestStreamCopyButtonsInAppJs:
    """Streamed assistant messages must have 复制 and Markdown buttons."""

    def test_has_copy_plain_button_for_stream(self):
        js = _read_app_js()
        assert "复制" in js and "msg-copy" in js, (
            "app.js must have 复制 button with msg-copy class for streamed messages"
        )

    def test_has_markdown_copy_button_for_stream(self):
        js = _read_app_js()
        assert "Markdown" in js, "app.js must have Markdown copy option for streamed messages"

    def test_has_msg_footer_for_streamed_message(self):
        js = _read_app_js()
        assert "msg-footer" in js, "app.js must add msg-footer (with copy actions) to streamed assistant message"


class TestIndexStructure:
    """index.html must provide messages area and deep thinking option."""

    def test_has_messages_container(self):
        html = _read_index()
        assert 'id="messages"' in html, "index.html must have #messages for conversation area"

    def test_has_deep_thinking_checkbox(self):
        html = _read_index()
        assert "deepThinkingCheckbox" in html or "深度思考" in html, (
            "index.html must have deep thinking checkbox (id or label)"
        )

    def test_has_deep_research_checkbox(self):
        html = _read_index()
        assert "deepResearchCheckbox" in html or "深度研究" in html, (
            "index.html must have deep research checkbox (id or label)"
        )

    def test_has_send_button(self):
        html = _read_index()
        assert "sendBtn" in html or "发送" in html, "index.html must have send button"

    def test_has_stop_button(self):
        html = _read_index()
        assert "stopBtn" in html or "停止" in html, "index.html must have stop button for aborting stream"

    def test_has_chat_and_code_tabs(self):
        html = _read_index()
        assert "tabChat" in html and "tabCode" in html, "index.html must have Chat and Code tab buttons"
        assert "pageChat" in html and "pageCode" in html, "index.html must have page panels for Chat and Code"

    def test_code_page_has_review_ui(self):
        html = _read_index()
        assert "codeReviewPath" in html or "Code Review" in html, "Code page must have code review path input or title"
        assert "codeReviewRunBtn" in html or "运行" in html, "Code page must have run code review button"

    def test_code_page_path_required(self):
        html = _read_index()
        assert "必填" in html or "codeReviewPath" in html, "Code page must indicate path is required"
        assert 'id="codeReviewPathError"' in html, "Code page must have path error element"

    def test_code_page_three_review_modes(self):
        html = _read_index()
        assert "全 repo" in html or "给定目录" in html, "Code page must have full-repo mode option"
        assert "按 Git 提交" in html or "codeReviewCommits" in html, "Code page must have git-commits mode"
        assert "当前变更" in html, "Code page must have uncommitted (current changes) mode"

    def test_code_page_git_error_area(self):
        html = _read_index()
        assert 'id="codeReviewGitError"' in html, "Code page must have git validation error area for red message"


class TestCodeReviewInAppJs:
    """app.js must implement path required, validate-commits for git mode, and error display."""

    def test_has_validate_commits_call(self):
        js = _read_app_js()
        assert "validate-commits" in js or "validate_commits" in js, (
            "app.js must call validate-commits API for git mode"
        )

    def test_has_show_path_error(self):
        js = _read_app_js()
        assert "showPathError" in js or "codeReviewPathError" in js, (
            "app.js must show path error when path empty"
        )

    def test_has_show_git_error(self):
        js = _read_app_js()
        assert "showGitError" in js or "codeReviewGitError" in js, (
            "app.js must show git validation error in red"
        )

    def test_path_required_check_before_run(self):
        js = _read_app_js()
        assert "!path" in js or "path.trim()" in js or 'pathEl' in js, (
            "app.js must check path before starting run"
        )


class TestCssStatsStyle:
    """style.css must style .msg-stats so it is visible."""

    def test_msg_stats_defined(self):
        css_path = STATIC_DIR / "style.css"
        assert css_path.exists(), "static/style.css must exist"
        css = css_path.read_text(encoding="utf-8")
        assert ".msg-stats" in css, "style.css must define .msg-stats for stats line"
