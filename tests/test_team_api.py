"""Tests for AI 员工团队 API: /api/tasks, /api/admin/roles, /api/abilities, /api/chat/room/*."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from memory_base.models_team import EmployeeRole, PromptVersion, RoleAbility


def _make_async_return(value):
    async def _():
        return value

    def _call(*args, **kwargs):
        return _()

    return _call


@pytest.fixture
def mock_db():
    """Mock DB for team routers (session_scope used by team_admin and team_room)."""
    async def noop_init_db():
        pass

    result_mock = MagicMock()
    result_mock.fetchall.return_value = []
    result_mock.scalars.return_value.all.return_value = []
    result_mock.scalars.return_value.first.return_value = None
    result_mock.scalar_one_or_none.return_value = None

    session_mock = MagicMock()
    session_mock.execute = _make_async_return(result_mock)
    session_mock.commit = _make_async_return(None)
    session_mock.rollback = _make_async_return(None)
    session_mock.add = lambda obj: None

    class Ctx:
        async def __aenter__(self):
            return session_mock

        async def __aexit__(self, *args):
            pass

    def session_scope():
        return Ctx()

    with patch("app.storage.db.init_db", side_effect=noop_init_db), patch(
        "app.storage.db.session_scope", new=session_scope
    ), patch("app.routers.team_admin.session_scope", new=session_scope), patch(
        "app.routers.team_room.session_scope", new=session_scope
    ):
        yield session_mock


def _stateful_session_scope(created_role_names: set):
    """Session scope that tracks created EmployeeRole names so duplicate create returns 400."""

    def get_execute_result(stmt):
        try:
            get_froms = getattr(stmt, "get_final_froms", None)
            froms = (get_froms() if callable(get_froms) else getattr(stmt, "froms", ())) or ()
            if froms and getattr(froms[0], "name", None) == "employee_roles":
                compiled = stmt.compile()
                params = getattr(compiled, "params", {}) or {}
                name_val = next(iter(params.values()), None) if params else None
                if name_val is not None and name_val in created_role_names:
                    result = MagicMock()
                    result.scalar_one_or_none.return_value = MagicMock()
                    result.scalars.return_value.all.return_value = []
                    result.fetchall.return_value = []
                    return result
        except Exception:
            pass
        result = MagicMock()
        result.fetchall.return_value = []
        result.scalars.return_value.all.return_value = []
        result.scalar_one_or_none.return_value = None
        return result

    def add(obj):
        if type(obj) is EmployeeRole:
            created_role_names.add(obj.name)

    class Ctx:
        async def __aenter__(self):
            session_mock = MagicMock()
            session_mock.add = add
            session_mock.execute = lambda stmt: _make_async_return(get_execute_result(stmt))()
            session_mock.commit = _make_async_return(None)
            session_mock.rollback = _make_async_return(None)
            return session_mock

        async def __aexit__(self, *args):
            pass

    def session_scope():
        return Ctx()

    return session_scope


@pytest.fixture
def stateful_mock_db():
    """Mock DB that tracks created EmployeeRole names so duplicate create returns 400."""
    created_role_names = set()
    scope = _stateful_session_scope(created_role_names)

    async def noop_init_db():
        pass

    with patch("app.storage.db.init_db", side_effect=noop_init_db), patch(
        "app.storage.db.session_scope", new=scope
    ), patch("app.routers.team_admin.session_scope", new=scope), patch(
        "app.routers.team_room.session_scope", new=scope
    ):
        yield scope


def _full_stateful_session_scope(store: dict):
    """
    Session scope for full CRUD: roles, abilities, prompts.
    store = {"roles": {}, "abilities": {}, "prompts": {}}
    """
    roles = store["roles"]
    abilities = store["abilities"]
    prompts = store["prompts"]

    def _table_name(stmt):
        try:
            get_froms = getattr(stmt, "get_final_froms", None)
            froms = (get_froms() if callable(get_froms) else getattr(stmt, "froms", ())) or ()
            if froms:
                return getattr(froms[0], "name", None)
        except Exception:
            pass
        return None

    def _param_value(stmt):
        try:
            compiled = stmt.compile()
            params = getattr(compiled, "params", {}) or {}
            return next(iter(params.values()), None) if params else None
        except Exception:
            return None

    def get_execute_result(stmt):
        try:
            tbl = getattr(stmt, "table", None)
            if tbl is not None and getattr(tbl, "name", None) == "role_abilities":
                name_val = _param_value(stmt)
                if name_val is not None:
                    abilities[name_val] = []
                result = MagicMock()
                result.scalar_one_or_none.return_value = None
                result.fetchall.return_value = []
                result.scalars.return_value.all.return_value = []
                return result

            table_name = _table_name(stmt)
            name_val = _param_value(stmt)

            if table_name == "employee_roles":
                if name_val is not None:
                    role = roles.get(name_val)
                    result = MagicMock()
                    result.scalar_one_or_none.return_value = role
                    result.scalars.return_value.all.return_value = [role] if role else []
                    result.fetchall.return_value = []
                    return result
                result = MagicMock()
                result.scalar_one_or_none.return_value = None
                result.scalars.return_value.all.return_value = list(roles.values())
                result.fetchall.return_value = []
                return result

            if table_name == "role_abilities":
                ab_list = abilities.get(name_val, []) if name_val else []
                result = MagicMock()
                result.scalar_one_or_none.return_value = None
                result.fetchall.return_value = [(a,) for a in ab_list]
                result.scalars.return_value.all.return_value = []
                return result

            if table_name == "prompt_versions" and name_val is not None:
                pr_list = prompts.get(name_val, [])
                latest = max(pr_list, key=lambda p: p[0], default=None)
                result = MagicMock()
                if latest is not None:
                    mock_pv = MagicMock()
                    mock_pv.version = latest[0]
                    mock_pv.content = latest[1]
                    result.scalar_one_or_none.return_value = mock_pv
                else:
                    result.scalar_one_or_none.return_value = None
                result.fetchall.return_value = []
                result.scalars.return_value.all.return_value = []
                return result
        except Exception:
            pass
        result = MagicMock()
        result.fetchall.return_value = []
        result.scalars.return_value.all.return_value = []
        result.scalar_one_or_none.return_value = None
        return result

    def add(obj):
        if type(obj) is EmployeeRole:
            roles[obj.name] = obj
        elif type(obj) is RoleAbility:
            abilities.setdefault(obj.role_name, []).append(obj.ability_id)
        elif type(obj) is PromptVersion:
            prompts.setdefault(obj.role_name, []).append((obj.version, obj.content))

    class Ctx:
        async def __aenter__(self):
            session_mock = MagicMock()
            session_mock.add = add
            session_mock.execute = lambda stmt: _make_async_return(get_execute_result(stmt))()
            session_mock.commit = _make_async_return(None)
            session_mock.rollback = _make_async_return(None)
            return session_mock

        async def __aexit__(self, *args):
            pass

    def session_scope():
        return Ctx()

    return session_scope


@pytest.fixture
def full_stateful_mock_db():
    """Full stateful mock: roles, abilities, prompts for CRUD flow (create -> GET -> PUT -> GET)."""
    store = {"roles": {}, "abilities": {}, "prompts": {}}
    scope = _full_stateful_session_scope(store)

    async def noop_init_db():
        pass

    with patch("app.storage.db.init_db", side_effect=noop_init_db), patch(
        "app.storage.db.session_scope", new=scope
    ), patch("app.routers.team_admin.session_scope", new=scope), patch(
        "app.routers.team_room.session_scope", new=scope
    ):
        yield scope


@pytest.fixture
def client(mock_db):
    with patch("app.main.validate_required_env"):
        from app.main import app
        with TestClient(app) as c:
            yield c


@pytest.fixture
def client_stateful(stateful_mock_db):
    """Client with stateful mock so create-then-get and duplicate-create behave correctly."""
    with patch("app.main.validate_required_env"):
        from app.main import app
        with TestClient(app) as c:
            yield c


@pytest.fixture
def client_full_stateful(full_stateful_mock_db):
    """Client with full stateful mock for CRUD: create -> GET -> PUT -> GET."""
    with patch("app.main.validate_required_env"):
        from app.main import app
        with TestClient(app) as c:
            yield c


def test_api_tasks(client):
    """GET /api/tasks returns 200 and list."""
    r = client.get("/api/tasks")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


def test_api_abilities(client):
    """GET /api/abilities returns 200 and list (from config local_tools)."""
    r = client.get("/api/abilities")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


def test_api_admin_roles_list(client):
    """GET /api/admin/roles returns 200 and list."""
    r = client.get("/api/admin/roles")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


def test_api_admin_roles_get_404(client):
    """GET /api/admin/roles/nonexistent returns 404."""
    r = client.get("/api/admin/roles/nonexistent")
    assert r.status_code == 404


def test_api_admin_roles_create(client):
    """POST /api/admin/roles creates role (mock DB returns no existing)."""
    r = client.post(
        "/api/admin/roles",
        json={
            "name": "test_analyst",
            "description": "测试角色",
            "status": "enabled",
            "abilities": [],
            "system_prompt": "You are a test analyst.",
        },
    )
    assert r.status_code == 200
    assert r.json().get("message") == "Role created successfully"


def test_api_chat_room_messages_400(client):
    """GET /api/chat/room/invalid/messages returns 400."""
    r = client.get("/api/chat/room/invalid/messages")
    assert r.status_code == 400


def test_api_chat_room_messages_200_empty(client):
    """GET /api/chat/room/{uuid}/messages returns 200 with list (empty when no messages)."""
    sid = str(uuid.uuid4())
    r = client.get("/api/chat/room/" + sid + "/messages")
    assert r.status_code == 200
    assert r.json() == []


def test_api_chat_room_message_post_404(client):
    """POST /api/chat/room/{uuid}/message with non-existent session returns 404."""
    sid = str(uuid.uuid4())
    r = client.post(
        "/api/chat/room/" + sid + "/message",
        json={"role": "user", "message": "hi", "message_type": "user_message"},
    )
    assert r.status_code == 404


def test_team_ui_mount(client):
    """GET /team/ returns 200 (team UI index)."""
    r = client.get("/team/")
    assert r.status_code == 200


def test_api_admin_roles_put_404(client):
    """PUT /api/admin/roles/nonexistent returns 404."""
    r = client.put(
        "/api/admin/roles/nonexistent",
        json={"description": "x", "system_prompt": "y"},
    )
    assert r.status_code == 404
    assert "detail" in r.json()


def test_api_admin_roles_create_duplicate_400(client_stateful):
    """POST /api/admin/roles with same name twice: second returns 400."""
    payload = {
        "name": "dup_role",
        "description": "First",
        "status": "enabled",
        "abilities": [],
        "system_prompt": "First prompt.",
    }
    r1 = client_stateful.post("/api/admin/roles", json=payload)
    assert r1.status_code == 200
    r2 = client_stateful.post("/api/admin/roles", json=payload)
    assert r2.status_code == 400
    assert "already exists" in (r2.json().get("detail") or "").lower()


def test_api_admin_roles_create_multiple_roles(client):
    """Create several employee roles (analyst, assistant); all return 200."""
    roles = [
        {"name": "analyst", "description": "数据分析师", "status": "enabled", "abilities": ["echo"], "system_prompt": "你是数据分析师。"},
        {"name": "assistant", "description": "通用助手", "status": "enabled", "abilities": [], "system_prompt": "你是助手。"},
    ]
    for role in roles:
        r = client.post("/api/admin/roles", json=role)
        assert r.status_code == 200, f"create {role['name']}: {r.json()}"
        assert r.json().get("message") == "Role created successfully"


def test_api_admin_roles_crud_full_flow(client_full_stateful):
    """Full CRUD: create role -> GET -> PUT update -> GET and assert updated (stateful mock)."""
    name = "crud_analyst"
    create_r = client_full_stateful.post(
        "/api/admin/roles",
        json={
            "name": name,
            "description": "Original",
            "status": "enabled",
            "abilities": ["echo"],
            "system_prompt": "You are an analyst. Original prompt.",
        },
    )
    assert create_r.status_code == 200

    get_r = client_full_stateful.get(f"/api/admin/roles/{name}")
    assert get_r.status_code == 200
    data = get_r.json()
    assert data["name"] == name
    assert data["description"] == "Original"
    assert data["status"] == "enabled"
    assert data["abilities"] == ["echo"]
    assert "Original prompt" in (data.get("system_prompt") or "")

    update_r = client_full_stateful.put(
        f"/api/admin/roles/{name}",
        json={
            "description": "Updated description",
            "system_prompt": "You are an analyst. Updated prompt.",
            "abilities": ["echo", "date"],
        },
    )
    assert update_r.status_code == 200

    get2_r = client_full_stateful.get(f"/api/admin/roles/{name}")
    assert get2_r.status_code == 200
    data2 = get2_r.json()
    assert data2["description"] == "Updated description"
    assert "Updated prompt" in (data2.get("system_prompt") or "")
    assert set(data2["abilities"]) == {"echo", "date"}


def test_api_admin_roles_list_after_create(client_full_stateful):
    """After creating roles, GET /api/admin/roles returns them (stateful list)."""
    client_full_stateful.post(
        "/api/admin/roles",
        json={"name": "list_a", "description": "A", "status": "enabled", "abilities": [], "system_prompt": "A"},
    )
    client_full_stateful.post(
        "/api/admin/roles",
        json={"name": "list_b", "description": "B", "status": "enabled", "abilities": ["echo"], "system_prompt": "B"},
    )
    r = client_full_stateful.get("/api/admin/roles")
    assert r.status_code == 200
    names = {x["name"] for x in r.json()}
    assert "list_a" in names
    assert "list_b" in names
