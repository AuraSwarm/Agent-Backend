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


class TestCssStatsStyle:
    """style.css must style .msg-stats so it is visible."""

    def test_msg_stats_defined(self):
        css_path = STATIC_DIR / "style.css"
        assert css_path.exists(), "static/style.css must exist"
        css = css_path.read_text(encoding="utf-8")
        assert ".msg-stats" in css, "style.css must define .msg-stats for stats line"
