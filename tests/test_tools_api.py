"""
Tests for local tools API: GET /tools, POST /tools/execute.

Uses real config (config/models.yaml or models.yaml.example) so local_tools must be defined there.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config.loader import get_config
from app.tools.runner import execute_local_tool, get_registered_tools


@pytest.fixture
def mock_db():
    """Mock DB so app lifespan runs without real Postgres."""
    async def noop_init_db():
        pass

    result_mock = MagicMock()
    result_mock.fetchall.return_value = []
    session_mock = MagicMock()
    session_mock.execute = AsyncMock(return_value=result_mock)
    session_mock.commit = AsyncMock(return_value=None)
    session_mock.rollback = AsyncMock(return_value=None)

    def add_and_assign_id(obj):
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()

    session_mock.add = add_and_assign_id
    session_mock.flush = AsyncMock(return_value=None)

    class Ctx:
        async def __aenter__(self):
            return session_mock

        async def __aexit__(self, *args):
            pass

    def session_scope():
        return Ctx()

    factory_mock = MagicMock()
    factory_mock.return_value = Ctx()

    with patch("app.main.init_db", side_effect=noop_init_db), patch(
        "app.main.get_session_factory", return_value=factory_mock
    ), patch("app.main.session_scope", new=session_scope), patch(
        "app.storage.db.get_session_factory", return_value=factory_mock
    ), patch("app.storage.db.session_scope", new=session_scope):
        yield session_mock


@pytest.fixture
def client(mock_db):
    """TestClient with mocked DB (for /tools no chat mock needed)."""
    with patch("app.main.validate_required_env"):
        from app.main import app

        with TestClient(app) as c:
            yield c


def test_list_tools(client: TestClient):
    """GET /tools returns 200 and list of {id, name, description}."""
    r = client.get("/tools")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    for item in data:
        assert "id" in item
        assert "name" in item
        assert "description" in item


def test_list_tools_matches_config(client: TestClient):
    """GET /tools returns same count as config local_tools."""
    config = get_config()
    registered = get_registered_tools(config)
    r = client.get("/tools")
    assert r.status_code == 200
    assert len(r.json()) == len(registered)


def test_execute_tool_echo(client: TestClient):
    """POST /tools/execute with tool_id=echo and message returns stdout."""
    r = client.post(
        "/tools/execute",
        json={"tool_id": "echo", "params": {"message": "hello"}},
    )
    assert r.status_code == 200
    data = r.json()
    assert "stdout" in data
    assert "stderr" in data
    assert "returncode" in data
    assert data["returncode"] == 0
    assert "hello" in data["stdout"]


def test_execute_tool_date(client: TestClient):
    """POST /tools/execute with tool_id=date (no params) returns 200 and returncode 0."""
    r = client.post(
        "/tools/execute",
        json={"tool_id": "date", "params": {}},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["returncode"] == 0
    assert "stdout" in data


def test_execute_tool_unknown(client: TestClient):
    """POST /tools/execute with unknown tool_id returns 400."""
    r = client.post(
        "/tools/execute",
        json={"tool_id": "nonexistent_tool", "params": {}},
    )
    assert r.status_code == 400
    assert "detail" in r.json()


def test_execute_tool_invalid_arg_rejected(client: TestClient):
    """POST /tools/execute with shell metacharacter in param returns 400."""
    r = client.post(
        "/tools/execute",
        json={"tool_id": "echo", "params": {"message": "x; rm -rf /"}},
    )
    assert r.status_code == 400
    assert "detail" in r.json()


def test_execute_tool_echo_no_params_fails(client: TestClient):
    """POST /tools/execute for echo without message param returns 400 (missing placeholder)."""
    r = client.post(
        "/tools/execute",
        json={"tool_id": "echo", "params": {}},
    )
    # echo tool has command ["echo", "{message}"] so missing param raises ValueError
    assert r.status_code == 400
