"""
Tests for short / medium / long term memory mechanisms.

- Short: session + messages in DB (POST /sessions, POST /chat, GET /sessions/{id}/messages).
- Medium: session summaries and archive (SessionSummary, messages_archive, archive task).
- Long: long-term storage backend (profile + knowledge via OSS or in-memory).
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.storage.long_term import (
    get_long_term_backend,
    is_long_term_oss,
    reset_long_term_backend,
)


def _make_async_return(value):
    async def _():
        return value

    def _call(*args, **kwargs):
        return _()

    return _call


@pytest.fixture(autouse=True)
def _reset_long_term():
    """Reset long-term backend before each test so config patches take effect."""
    reset_long_term_backend()
    yield
    reset_long_term_backend()


# ---- Short-term: session + messages ----
def test_short_term_memory_session_and_messages():
    """Short-term: session + message models and DB scope (mechanism wired)."""
    from memory_base import Message, Session

    from app.storage import db

    # Assert short-term models and session_scope exist; full E2E is in test_main_api.
    assert hasattr(Session, "id")
    assert hasattr(Message, "session_id")
    assert hasattr(Message, "content")
    assert db.session_scope is not None


# ---- Medium-term: summaries and archive ----
def test_medium_term_memory_models_and_task():
    """Medium-term: SessionSummary and MessageArchive exist; archive task is registered."""
    from memory_base import SessionSummary
    from memory_base.models_archive import MessageArchive

    from app.tasks.archive_tasks import archive_by_activity

    assert hasattr(SessionSummary, "session_id")
    assert hasattr(SessionSummary, "summary_json")
    assert hasattr(MessageArchive, "session_id")
    assert hasattr(MessageArchive, "content")
    assert callable(archive_by_activity)


# ---- Long-term: backend from config ----
def test_long_term_memory_backend_in_memory_when_no_oss():
    """Long-term: when OSS is not configured, backend is InMemoryLongTermStorage."""
    from memory_base.long_term_storage import InMemoryLongTermStorage

    settings_mock = MagicMock()
    settings_mock.model_dump.return_value = {"config_dir": "config"}
    with patch("app.storage.long_term.get_app_settings", return_value=settings_mock):
        reset_long_term_backend()
        backend = get_long_term_backend()
    assert isinstance(backend, InMemoryLongTermStorage)


def test_long_term_memory_backend_oss_when_configured():
    """Long-term: when OSS credentials are in config, backend is OssStorage."""
    from memory_base.long_term_storage import OssStorage

    settings_mock = MagicMock()
    settings_mock.model_dump.return_value = {
        "oss_endpoint": "https://oss-cn-beijing.aliyuncs.com",
        "oss_bucket": "test-bucket",
        "oss_access_key_id": "key",
        "oss_access_key_secret": "secret",
    }
    with patch("app.storage.long_term.get_app_settings", return_value=settings_mock):
        reset_long_term_backend()
        backend = get_long_term_backend()
    assert isinstance(backend, OssStorage)
    assert backend.bucket_name == "test-bucket"


def test_long_term_memory_save_load_profile_and_knowledge():
    """Long-term: save/load profile and triples via backend (in-memory)."""
    from memory_base import (
        load_user_profile,
        retrieve_relevant_knowledge,
        save_knowledge_triples,
        save_user_profile,
    )

    settings_mock = MagicMock()
    settings_mock.model_dump.return_value = {}
    with patch("app.storage.long_term.get_app_settings", return_value=settings_mock):
        reset_long_term_backend()
        backend = get_long_term_backend()

    user_id = "test_user_" + uuid.uuid4().hex[:8]
    profile = {"user_id": user_id, "traits": {"communication_style": "concise"}}
    save_user_profile(backend, user_id, profile)
    loaded = load_user_profile(backend, user_id)
    assert loaded is not None
    assert loaded.get("traits", {}).get("communication_style") == "concise"

    triples = [("用户", "使用", "Aura"), ("用户", "部署", "OSS")]
    save_knowledge_triples(backend, user_id, triples)
    relevant = retrieve_relevant_knowledge(backend, user_id, "Aura", top_k=5)
    assert len(relevant) >= 1
    assert any("Aura" in str(t) for t in relevant)


def test_long_term_oss_flag():
    """is_long_term_oss() is True only when backend is OssStorage."""
    settings_mock = MagicMock()
    settings_mock.model_dump.return_value = {}
    with patch("app.storage.long_term.get_app_settings", return_value=settings_mock):
        reset_long_term_backend()
        assert is_long_term_oss() is False

    settings_mock.model_dump.return_value = {
        "oss_endpoint": "https://oss-cn-beijing.aliyuncs.com",
        "oss_bucket": "b",
        "oss_access_key_id": "k",
        "oss_access_key_secret": "s",
    }
    with patch("app.storage.long_term.get_app_settings", return_value=settings_mock):
        reset_long_term_backend()
        assert is_long_term_oss() is True
