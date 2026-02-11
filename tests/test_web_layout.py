"""
Tests for web UI layout: prevent whole-page scroll.

Ensures static/style.css and index.html keep the contract:
- Document (html/body) must not scroll: overflow hidden, max-height 100vh.
- Left sidebar and right app-main must be position: fixed.
- Only main#messages and .conversation-list-scroll may have overflow-y: auto.

Run: pytest tests/test_web_layout.py -v
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
STYLE_PATH = STATIC_DIR / "style.css"
INDEX_PATH = STATIC_DIR / "index.html"


def _read_css() -> str:
    assert STYLE_PATH.exists(), f"Missing {STYLE_PATH}"
    return STYLE_PATH.read_text(encoding="utf-8")


def _read_html() -> str:
    assert INDEX_PATH.exists(), f"Missing {INDEX_PATH}"
    return INDEX_PATH.read_text(encoding="utf-8")


def _css_has_rule(css: str, selector: str, prop: str, value_substr: str) -> bool:
    """Check that a selector block contains a property whose value contains value_substr."""
    # Simple check: after the selector, find a block {} and see if prop: value_substr appears
    pattern = re.escape(selector) + r"\s*\{[^}]*" + re.escape(prop) + r"\s*:[^}]*" + re.escape(value_substr)
    return bool(re.search(pattern, css, re.DOTALL))


class TestNoWholePageScroll:
    """Layout must not allow document-level scroll."""

    def test_html_overflow_hidden(self):
        css = _read_css()
        assert "overflow" in css and "hidden" in css, "style.css must define overflow"
        # html block should contain overflow: hidden
        assert re.search(r"html\s*\{[^}]*overflow\s*:\s*hidden", css, re.DOTALL), (
            "html must have overflow: hidden to prevent whole-page scroll"
        )

    def test_body_overflow_hidden(self):
        css = _read_css()
        assert re.search(r"body\s*\{[^}]*overflow\s*:\s*hidden", css, re.DOTALL), (
            "body must have overflow: hidden to prevent whole-page scroll"
        )

    def test_body_max_height_constrained(self):
        css = _read_css()
        assert "max-height" in css and "100" in css, (
            "body or html should constrain max-height (e.g. 100vh) so document cannot grow"
        )

    def test_sidebar_position_fixed(self):
        css = _read_css()
        assert ".sidebar" in css, "sidebar class must exist"
        # .sidebar block must have position: fixed
        assert re.search(r"\.sidebar\s*\{[^}]*position\s*:\s*fixed", css, re.DOTALL), (
            ".sidebar must be position: fixed so left panel does not scroll with page"
        )

    def test_app_main_position_fixed(self):
        css = _read_css()
        assert ".app-main" in css or "app-main" in css, "app-main must exist"
        assert re.search(r"\.app-main\s*\{[^}]*position\s*:\s*fixed", css, re.DOTALL), (
            ".app-main must be position: fixed so right panel (header/footer) does not scroll"
        )

    def test_main_has_overflow_y_auto(self):
        css = _read_css()
        assert re.search(r"main\s*\{[^}]*overflow-y\s*:\s*auto", css, re.DOTALL), (
            "main must have overflow-y: auto so only conversation area scrolls"
        )

    def test_main_has_min_height_zero(self):
        css = _read_css()
        assert re.search(r"main\s*\{[^}]*min-height\s*:\s*0", css, re.DOTALL), (
            "main must have min-height: 0 for flex child to allow internal scroll"
        )

    def test_conversation_list_scroll_has_overflow_y_auto(self):
        css = _read_css()
        assert "conversation-list-scroll" in css or "conversation_list_scroll" in css, (
            "conversation-list-scroll must exist for left sidebar independent scroll"
        )
        assert re.search(
            r"\.conversation-list-scroll\s*\{[^}]*overflow-y\s*:\s*auto",
            css,
            re.DOTALL,
        ), (
            ".conversation-list-scroll must have overflow-y: auto for independent list scroll"
        )

    def test_layout_root_overflow_hidden(self):
        css = _read_css()
        assert re.search(
            r"\.layout-root\s*\{[^}]*overflow\s*:\s*hidden",
            css,
            re.DOTALL,
        ), (
            ".layout-root must have overflow: hidden as second line of defense against document scroll"
        )


class TestHtmlStructure:
    """HTML must provide the scroll containers."""

    def test_has_conversation_list_scroll_wrapper(self):
        html = _read_html()
        assert "conversationListScroll" in html or "conversation-list-scroll" in html, (
            "index.html must have conversation list inside a scroll wrapper (id or class)"
        )

    def test_has_messages_main(self):
        html = _read_html()
        assert 'id="messages"' in html, "index.html must have main#messages as the only scroll area for conversation"

    def test_has_sidebar_and_app_main(self):
        html = _read_html()
        assert "sidebar" in html and "app-main" in html, (
            "index.html must have .sidebar and .app-main for fixed layout"
        )

    def test_has_layout_root_wrapper(self):
        html = _read_html()
        assert "layout-root" in html, (
            "index.html must have #layout-root wrapper to constrain overflow"
        )
