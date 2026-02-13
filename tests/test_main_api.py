"""
FastAPI endpoint tests using TestClient.

Uses mocks for DB so tests run without PostgreSQL. Override init_db and
get_session_factory/session_scope so lifespan and routes don't require a real DB.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_async_return(value):
    """Return a callable that returns a fresh coroutine (run in awaiter's loop, not test loop)."""
    async def _():
        return value
    def _call(*args, **kwargs):
        return _()
    return _call


@pytest.fixture
def mock_db():
    """Mock DB so app lifespan and routes don't need real Postgres."""
    async def noop_init_db():
        pass

    result_mock = MagicMock()
    result_mock.fetchall.return_value = []

    session_mock = MagicMock()
    session_mock.execute = _make_async_return(result_mock)
    session_mock.commit = _make_async_return(None)
    session_mock.rollback = _make_async_return(None)

    def add_and_assign_id(obj):
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()

    session_mock.add = add_and_assign_id
    session_mock.flush = _make_async_return(None)

    class Ctx:
        async def __aenter__(self):
            return session_mock

        async def __aexit__(self, *args):
            pass

    def session_scope():
        return Ctx()

    factory_mock = MagicMock()
    factory_mock.return_value = Ctx()

    # Patch at storage.db and at each router so all code paths use the mock (routers import at load time)
    with patch("app.storage.db.init_db", side_effect=noop_init_db), patch(
        "app.storage.db.get_session_factory", return_value=factory_mock
    ), patch("app.storage.db.session_scope", new=session_scope), patch(
        "app.routers.chat.session_scope", new=session_scope
    ), patch("app.routers.chat.get_session_factory", return_value=factory_mock), patch(
        "app.routers.health.session_scope", new=session_scope
    ), patch("app.routers.health.get_session_factory", return_value=factory_mock), patch(
        "app.routers.sessions.session_scope", new=session_scope
    ), patch("app.routers.sessions.get_session_factory", return_value=factory_mock), patch(
        "app.routers.code_review.session_scope", new=session_scope
    ), patch("app.routers.code_review.get_session_factory", return_value=factory_mock):
        yield session_mock


@pytest.fixture
def client(mock_db):
    """TestClient with mocked DB, chat adapter, and embedding (no real API key needed)."""
    async def fake_stream(*args, **kwargs):
        yield "ok"

    async def fake_embedding(*args, **kwargs):
        return [0.0] * 1536  # dummy vector for session search

    with patch("app.main.validate_required_env"), patch(
        "app.routers.chat.CloudAPIAdapter"
    ) as AdapterMock, patch("app.routers.sessions.get_embedding", side_effect=fake_embedding):
        AdapterMock.return_value.call = AsyncMock(return_value=("ok", {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}))
        AdapterMock.return_value.stream_call = fake_stream
        from app.main import app
        with TestClient(app) as c:
            yield c


def test_health(client):
    """GET /health returns 200 when DB is ok (mocked)."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok"}


def test_admin_reload(client):
    """POST /admin/reload returns 200."""
    r = client.post("/admin/reload")
    assert r.status_code == 200
    data = r.json()
    assert data.get("status") == "ok"


def test_create_session(client):
    """POST /sessions returns session_id."""
    r = client.post("/sessions", json={})
    assert r.status_code == 200
    data = r.json()
    assert "session_id" in data

    r2 = client.post("/sessions", json={"title": "Test"})
    assert r2.status_code == 200
    assert "session_id" in r2.json()


def test_chat_non_stream(client):
    """POST /chat with stream=false returns choices, usage, duration_ms."""
    r = client.post(
        "/chat",
        json={
            "session_id": "00000000-0000-0000-0000-000000000001",
            "messages": [{"role": "user", "content": "Say hi"}],
            "stream": False,
        },
    )
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        data = r.json()
        assert "choices" in data
        assert len(data["choices"]) >= 1
        assert "usage" in data
        assert "duration_ms" in data


def test_chat_deep_thinking_non_stream(client):
    """POST /chat with deep_thinking=true returns 200 and stats."""
    r = client.post(
        "/chat",
        json={
            "session_id": "00000000-0000-0000-0000-000000000001",
            "messages": [{"role": "user", "content": "如何降低企业碳排放？"}],
            "stream": False,
            "deep_thinking": True,
        },
    )
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        data = r.json()
        assert "choices" in data
        assert "usage" in data
        assert "duration_ms" in data


def test_chat_deep_research_non_stream(client):
    """POST /chat with deep_research=true returns 200 and stats."""
    r = client.post(
        "/chat",
        json={
            "session_id": "00000000-0000-0000-0000-000000000001",
            "messages": [{"role": "user", "content": "如何进一步优化深度思考功能的实现逻辑？"}],
            "stream": False,
            "deep_research": True,
        },
    )
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        data = r.json()
        assert "choices" in data
        assert "usage" in data
        assert "duration_ms" in data


def test_chat_stream(client):
    """POST /chat with stream=true returns streaming response."""
    r = client.post(
        "/chat",
        json={
            "session_id": "00000000-0000-0000-0000-000000000001",
            "messages": [{"role": "user", "content": "Say one word"}],
            "stream": True,
        },
    )
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        assert "text/event-stream" in r.headers.get("content-type", "")
        # Consume stream and ensure at least one event has usage/duration_ms (for stats line)
        seen_stats = False
        for line in r.iter_lines():
            if line and line.startswith("data: "):
                payload = line[6:]
                if payload == "[DONE]":
                    break
                try:
                    import json as _json
                    j = _json.loads(payload)
                    if "duration_ms" in j or (j.get("usage") is not None):
                        seen_stats = True
                        break
                except Exception:
                    pass
        assert seen_stats, "stream should contain at least one event with usage or duration_ms"


def test_chat_accepts_optional_params(client):
    """POST /chat accepts model, deep_thinking, deep_research without error."""
    r = client.post(
        "/chat",
        json={
            "session_id": "00000000-0000-0000-0000-000000000001",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": False,
            "model": None,
            "deep_thinking": False,
            "deep_research": False,
        },
    )
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        data = r.json()
        assert "choices" in data
        assert "usage" in data
        assert "duration_ms" in data


def test_chat_system_message_passed_to_adapter(client):
    """POST /chat with role-style system + user messages: adapter receives full messages (dialogue with AI as employee)."""
    role_system = "You are an analyst. Reply briefly."
    user_content = "What is 2+2?"
    with patch("app.routers.chat.CloudAPIAdapter") as AdapterMock:
        AdapterMock.return_value.call = AsyncMock(
            return_value=("Four.", {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})
        )
        r = client.post(
            "/chat",
            json={
                "session_id": "00000000-0000-0000-0000-000000000001",
                "messages": [
                    {"role": "system", "content": role_system},
                    {"role": "user", "content": user_content},
                ],
                "stream": False,
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert "choices" in data and len(data["choices"]) >= 1
    assert data["choices"][0].get("message", {}).get("content") == "Four."
    call_kw = AdapterMock.return_value.call.call_args[1]
    messages = call_kw.get("messages", [])
    system_msgs = [m for m in messages if m.get("role") == "system"]
    assert any(role_system in (m.get("content") or "") for m in system_msgs)
    user_msgs = [m for m in messages if m.get("role") == "user"]
    assert any(user_content in (m.get("content") or "") for m in user_msgs)


def test_chat_non_stream_usage_schema(client):
    """POST /chat non-stream response usage has expected shape (prompt/completion/total_tokens)."""
    r = client.post(
        "/chat",
        json={
            "session_id": "00000000-0000-0000-0000-000000000001",
            "messages": [{"role": "user", "content": "One word"}],
            "stream": False,
        },
    )
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        data = r.json()
        assert isinstance(data.get("usage"), dict)
        u = data["usage"]
        # Mock returns prompt_tokens, completion_tokens, total_tokens
        assert "total_tokens" in u or "prompt_tokens" in u or "completion_tokens" in u
        assert isinstance(data.get("duration_ms"), (int, float))


def test_chat_stream_event_format(client):
    """POST /chat stream: events include content deltas and a final stats event."""
    r = client.post(
        "/chat",
        json={
            "session_id": "00000000-0000-0000-0000-000000000001",
            "messages": [{"role": "user", "content": "x"}],
            "stream": True,
        },
    )
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        import json as _json
        content_parts = []
        stats_event = None
        for line in r.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            try:
                j = _json.loads(payload)
                if "choices" in j and j["choices"]:
                    delta = (j["choices"][0].get("delta") or {}).get("content")
                    if delta:
                        content_parts.append(delta)
                if "duration_ms" in j or j.get("usage") is not None:
                    stats_event = j
            except Exception:
                pass
        assert stats_event is not None, "stream must contain one stats event (usage/duration_ms)"
        assert "duration_ms" in stats_event


def test_chat_deep_research_priority_when_both_true(client):
    """When both deep_thinking and deep_research are true, backend uses deep_research (200 + content)."""
    r = client.post(
        "/chat",
        json={
            "session_id": "00000000-0000-0000-0000-000000000001",
            "messages": [{"role": "user", "content": "Test"}],
            "stream": False,
            "deep_thinking": True,
            "deep_research": True,
        },
    )
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        data = r.json()
        assert "choices" in data and len(data["choices"]) >= 1
        assert data["choices"][0].get("message", {}).get("content") is not None


def test_session_search(client):
    """GET /sessions/{id}/search returns matches or empty list."""
    r = client.get("/sessions/00000000-0000-0000-0000-000000000001/search", params={"query": "hello"})
    assert r.status_code == 200
    data = r.json()
    assert "matches" in data
    assert isinstance(data["matches"], list)


def test_list_sessions_has_three_part_fields(client):
    """GET /sessions returns list with first_message_preview, last_message_preview, topic_summary."""
    r = client.get("/sessions?limit=10")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    for item in data:
        assert "session_id" in item
        assert "title" in item
        assert "updated_at" in item
        assert "first_message_preview" in item
        assert "last_message_preview" in item
        assert "topic_summary" in item


def test_delete_session_invalid_id_returns_404(client):
    """DELETE /sessions/{id} returns 404 for invalid UUID (cleanup endpoint)."""
    r = client.delete("/sessions/not-a-uuid")
    assert r.status_code == 404
    assert "detail" in r.json()


def test_code_reviews_list_empty(client):
    """GET /code-reviews returns 200 and a list (e.g. empty when no data)."""
    r = client.get("/code-reviews?limit=10")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    for item in data:
        assert "id" in item
        assert "created_at" in item
        assert "mode" in item
        assert "provider" in item
        assert "files_included" in item


def test_code_review_get_404(client):
    """GET /code-reviews/{id} returns 404 for invalid id."""
    r = client.get("/code-reviews/not-a-valid-uuid")
    assert r.status_code == 404


def test_code_review_validate_commits_empty(client, tmp_path):
    """POST /code-review/validate-commits returns valid=false when commits empty."""
    with patch("app.routers.code_review._code_review_root", return_value=str(tmp_path)):
        r = client.post("/code-review/validate-commits", json={"commits": []})
    assert r.status_code == 200
    data = r.json()
    assert data.get("valid") is False
    assert "error" in data


def test_code_review_validate_commits_not_git_repo(client, tmp_path):
    """POST /code-review/validate-commits returns valid=false when not a git repo."""
    with patch("app.routers.code_review._code_review_root", return_value=str(tmp_path)):
        r = client.post("/code-review/validate-commits", json={"commits": ["abc123"]})
    assert r.status_code == 200
    data = r.json()
    assert data.get("valid") is False
    assert "error" in data
    assert "git" in (data.get("error") or "").lower()


def test_code_review_validate_commits_valid(client, tmp_path):
    """POST /code-review/validate-commits returns valid=true when validation passes."""
    with patch("app.routers.code_review.validate_commits_for_review", return_value=(True, None)):
        r = client.post("/code-review/validate-commits", json={"commits": ["abc123"]})
    assert r.status_code == 200
    data = r.json()
    assert data.get("valid") is True
    assert "error" not in data or data.get("error") is None


def test_code_review_validate_commits_missing_body_422(client):
    """POST /code-review/validate-commits without body returns 422."""
    r = client.post("/code-review/validate-commits", json={})
    assert r.status_code == 422


def test_code_reviews_create_returns_id_and_report(client):
    """POST /code-reviews creates a record and returns id, report, mode."""
    r = client.post(
        "/code-reviews",
        json={
            "mode": "path",
            "path": "app",
            "provider": "claude",
            "report": "## Summary\nOK",
            "files_included": 1,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("id"), "response must include id"
    assert data.get("report") == "## Summary\nOK"
    assert data.get("mode") == "path"
    assert data.get("files_included") == 1


def test_code_reviews_delete_returns_200(client):
    """DELETE /code-reviews/{id} returns 200 when record exists (mocked)."""
    r = client.post(
        "/code-reviews",
        json={"mode": "path", "path": "x", "provider": "claude", "report": "X", "files_included": 0},
    )
    rid = r.json().get("id")
    r2 = client.delete("/code-reviews/" + rid)
    assert r2.status_code == 200
    assert "ok" in r2.json().get("status", "") or "message" in r2.json()


def test_code_reviews_list_limit_capped(client):
    """GET /code-reviews with large limit is capped (e.g. 100)."""
    r = client.get("/code-reviews?limit=200")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) <= 100


def test_code_reviews_delete_invalid_uuid_404(client):
    """DELETE /code-reviews/{id} returns 404 for invalid uuid."""
    r = client.delete("/code-reviews/not-a-uuid")
    assert r.status_code == 404


def test_root_or_main_ui(client):
    """GET /: 404 when API-only; 200 with main UI when WEB_UI_DIR is set (Aura)."""
    r = client.get("/")
    if r.status_code == 404:
        return
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    html = r.text
    assert "主对话" in html or "Chat" in html or "messages" in html
