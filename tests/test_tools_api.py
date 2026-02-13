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

    with patch("app.storage.db.init_db", side_effect=noop_init_db), patch(
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


def test_execute_each_registered_tool(client: TestClient):
    """Execute every registered tool with valid params; all return 200 and returncode 0 (covers all tools)."""
    r = client.get("/tools")
    assert r.status_code == 200
    tools = r.json()
    assert isinstance(tools, list)
    for t in tools:
        tool_id = t.get("id")
        assert tool_id
        if tool_id == "echo":
            payload = {"tool_id": "echo", "params": {"message": "test_execute_each"}}
        elif tool_id == "date":
            payload = {"tool_id": "date", "params": {}}
        else:
            # Unknown tool from config: skip or use empty params
            payload = {"tool_id": tool_id, "params": {}}
        exec_r = client.post("/tools/execute", json=payload)
        assert exec_r.status_code == 200, f"tool_id={tool_id} failed: {exec_r.json()}"
        data = exec_r.json()
        assert "returncode" in data
        assert data["returncode"] == 0, f"tool_id={tool_id} returncode={data.get('returncode')} stderr={data.get('stderr')}"
        assert "stdout" in data


def test_api_abilities_matches_tools_and_executable(client: TestClient):
    """GET /api/abilities returns same tool ids as GET /tools; each can be executed (role binding uses abilities)."""
    tools_r = client.get("/tools")
    abilities_r = client.get("/api/abilities")
    assert tools_r.status_code == 200 and abilities_r.status_code == 200
    tools = {t["id"] for t in tools_r.json()}
    abilities = {a["id"] for a in abilities_r.json()}
    assert abilities == tools, "abilities should match local_tools for role binding"
    for aid in abilities:
        if aid == "echo":
            exec_r = client.post("/tools/execute", json={"tool_id": aid, "params": {"message": "ok"}})
        else:
            exec_r = client.post("/tools/execute", json={"tool_id": aid, "params": {}})
        assert exec_r.status_code == 200
        assert exec_r.json().get("returncode") == 0


def test_tools_execute_missing_tool_id_422(client: TestClient):
    """POST /tools/execute without tool_id returns 422."""
    r = client.post("/tools/execute", json={"params": {}})
    assert r.status_code == 422


def test_tools_execute_params_null_treated_as_empty(client: TestClient):
    """POST /tools/execute with params=null is accepted (treated as empty for date)."""
    r = client.post("/tools/execute", json={"tool_id": "date", "params": None})
    assert r.status_code == 200
    assert r.json().get("returncode") == 0


def test_api_abilities_each_has_id_name_description(client: TestClient):
    """GET /api/abilities: each item has id, name, description (boundary schema)."""
    r = client.get("/api/abilities")
    assert r.status_code == 200
    for item in r.json():
        assert "id" in item
        assert "name" in item
        assert "description" in item
        assert isinstance(item["id"], str)
        assert isinstance(item.get("name"), str)
        assert isinstance(item.get("description"), str)


def test_tools_execute_echo_empty_message_boundary(client: TestClient):
    """POST /tools/execute echo with empty string: either 200 (accepted) or 400 (rejected by validation)."""
    r = client.post("/tools/execute", json={"tool_id": "echo", "params": {"message": ""}})
    assert r.status_code in (200, 400)
    if r.status_code == 200:
        assert r.json().get("returncode") == 0
        assert "stdout" in r.json()
    else:
        assert "detail" in r.json()
