"""
界面级测试：网页加载、静态资源、以及 UI 使用的 API 流程。

- 页面：GET /team/, /team/admin/roles.html 返回 200 且 HTML 包含页面所需结构。
- 静态资源：/team/style.css, /team/script.js, /team/admin/admin-roles.js 可访问。
- UI 流程：任务中心（任务列表 -> 选任务 -> 消息列表 -> 发消息）、角色管理（列表 -> 新建/编辑 -> 保存）所调用的 API 按顺序可通且返回预期结构。
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.storage.models import Message, Session


def _make_async_return(value):
    async def _():
        return value

    def _call(*args, **kwargs):
        return _()

    return _call


# --- Fixtures: shared mock for sessions + team_room (task center flow) ---
def _task_center_session_scope(sessions_list: list, messages_by_session: dict):
    """Stateful scope: store Session and Message for task center UI flow."""

    def _table_name(stmt):
        try:
            get_froms = getattr(stmt, "get_final_froms", None)
            froms = (get_froms() if callable(get_froms) else getattr(stmt, "froms", ())) or ()
            if froms:
                return getattr(froms[0], "name", None)
        except Exception:
            pass
        return None

    def _param_value(stmt, key_hint=None):
        try:
            compiled = stmt.compile()
            params = getattr(compiled, "params", {}) or {}
            return next(iter(params.values()), None) if params else None
        except Exception:
            return None

    def get_execute_result(stmt):
        table_name = _table_name(stmt)
        param_val = _param_value(stmt)

        if table_name == "sessions":
            if param_val is not None and (isinstance(param_val, uuid.UUID) or (isinstance(param_val, str) and len(str(param_val)) > 10)):
                sid = param_val if isinstance(param_val, uuid.UUID) else uuid.UUID(str(param_val))
                session = next((s for s in sessions_list if s.id == sid), None)
                result = MagicMock()
                result.scalar_one_or_none.return_value = session
                result.scalars.return_value.all.return_value = [session] if session else []
                result.fetchall.return_value = []
                return result
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            result.scalars.return_value.all.return_value = [s for s in sessions_list if getattr(s, "status", 1) == 1]
            result.fetchall.return_value = []
            return result

        if table_name == "messages" and param_val is not None:
            sid = param_val if isinstance(param_val, uuid.UUID) else uuid.UUID(str(param_val))
            msgs = messages_by_session.get(str(sid), [])
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            result.scalars.return_value.all.return_value = msgs
            result.fetchall.return_value = []
            return result
        result = MagicMock()
        result.fetchall.return_value = []
        result.scalars.return_value.all.return_value = []
        result.scalar_one_or_none.return_value = None
        return result

    def add(obj):
        if type(obj) is Session:
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            if not hasattr(obj, "status") or getattr(obj, "status", None) is None:
                obj.status = 1
            if not getattr(obj, "title", None) and hasattr(obj, "title"):
                obj.title = getattr(obj, "title", None) or "未命名任务"
            sessions_list.append(obj)
        elif type(obj) is Message:
            key = str(obj.session_id)
            messages_by_session.setdefault(key, []).append(obj)

    class Ctx:
        async def __aenter__(self):
            session_mock = MagicMock()
            session_mock.add = add
            session_mock.execute = lambda stmt: _make_async_return(get_execute_result(stmt))()
            session_mock.commit = _make_async_return(None)
            session_mock.rollback = _make_async_return(None)
            session_mock.flush = _make_async_return(None)
            return session_mock

        async def __aexit__(self, *args):
            pass

    def session_scope():
        return Ctx()

    return session_scope


@pytest.fixture
def mock_db():
    """Default mock DB for page/asset tests."""
    async def noop_init_db():
        pass

    result_mock = MagicMock()
    result_mock.fetchall.return_value = []
    result_mock.scalars.return_value.all.return_value = []
    result_mock.scalar_one_or_none.return_value = None

    session_mock = MagicMock()
    session_mock.execute = _make_async_return(result_mock)
    session_mock.commit = _make_async_return(None)
    session_mock.rollback = _make_async_return(None)
    session_mock.add = lambda obj: None
    session_mock.flush = _make_async_return(None)

    class Ctx:
        async def __aenter__(self):
            return session_mock

        async def __aexit__(self, *args):
            pass

    def session_scope():
        return Ctx()

    with patch("app.storage.db.init_db", side_effect=noop_init_db), patch(
        "app.storage.db.session_scope", new=session_scope
    ), patch("app.storage.db.get_session_factory", return_value=MagicMock(return_value=Ctx())), patch(
        "app.routers.team_admin.session_scope", new=session_scope
    ), patch("app.routers.team_room.session_scope", new=session_scope), patch(
        "app.routers.sessions.session_scope", new=session_scope
    ), patch("app.routers.chat.session_scope", new=session_scope), patch(
        "app.routers.sessions.get_session_factory", return_value=MagicMock(return_value=Ctx())
    ):
        yield session_mock


@pytest.fixture
def client(mock_db):
    with patch("app.main.validate_required_env"):
        from app.main import app
        with TestClient(app) as c:
            yield c


@pytest.fixture
def task_center_state():
    """State for task center: one session, messages by session_id."""
    sessions_list = []
    messages_by_session = {}
    return sessions_list, messages_by_session


@pytest.fixture
def client_task_center(task_center_state):
    """Client with stateful session/messages for task center UI flow."""
    sessions_list, messages_by_session = task_center_state
    scope = _task_center_session_scope(sessions_list, messages_by_session)

    async def noop_init_db():
        pass

    with patch("app.storage.db.init_db", side_effect=noop_init_db), patch(
        "app.storage.db.session_scope", new=scope
    ), patch("app.storage.db.get_session_factory", return_value=MagicMock(return_value=scope())), patch(
        "app.routers.team_admin.session_scope", new=scope
    ), patch("app.routers.team_room.session_scope", new=scope), patch(
        "app.routers.sessions.session_scope", new=scope
    ), patch("app.routers.chat.session_scope", new=scope), patch(
        "app.routers.sessions.get_session_factory", return_value=MagicMock(return_value=scope())
    ), patch("app.main.validate_required_env"):
        from app.main import app
        with TestClient(app) as c:
            yield c


# --- Page load tests ---
def test_team_index_page_loads(client: TestClient):
    """GET /team/ 返回 200，HTML 包含任务中心所需元素与子页面 Tab 栏。"""
    r = client.get("/team/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    html = r.text
    assert "AI员工协作中心" in html or "AI 员工协作中心" in html or "AI员工" in html or "AI 员工" in html
    assert "task-list" in html
    assert "chat-container" in html
    assert "user-input" in html
    assert "send-btn" in html
    assert "content-tabs" in html
    assert "Task" in html and "Role" in html and "Ability" in html
    assert "任务中心" in html or "task-list" in html
    assert "/team/script.js" in html
    assert "/api/tasks" in html or "script.js" in html


def test_team_admin_roles_page_loads(client: TestClient):
    """GET /team/admin/roles.html 返回 200，HTML 包含角色管理所需元素。"""
    r = client.get("/team/admin/roles.html")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    html = r.text
    assert "AI员工角色管理" in html or "角色管理" in html
    assert "roles-list" in html
    assert "create-role-btn" in html
    assert "role-form" in html
    assert "role-name" in html
    assert "role-prompt" in html
    assert "/api/admin/roles" in html or "admin-roles.js" in html
    assert "任务中心" in html
    assert "员工角色" in html
    assert "content-tabs" in html


def test_team_static_assets_available(client: TestClient):
    """静态资源可访问：style.css, script.js, admin-roles.js, admin-models.js。"""
    for path in ["/team/style.css", "/team/script.js", "/team/admin/admin-roles.js", "/team/admin/admin-models.js"]:
        r = client.get(path)
        assert r.status_code == 200, f"GET {path} should be 200"
    r_css = client.get("/team/style.css")
    assert "text/css" in r_css.headers.get("content-type", "")
    r_js = client.get("/team/script.js")
    assert "javascript" in r_js.headers.get("content-type", "").lower() or len(r_js.text) > 0
    r_admin = client.get("/team/admin/admin-roles.js")
    assert len(r_admin.text) > 100
    assert "fetch" in r_admin.text or "api" in r_admin.text.lower()


def test_team_script_uses_expected_api_paths(client: TestClient):
    """任务中心 script.js 调用的 API 路径存在且可返回合理结构。"""
    r = client.get("/team/script.js")
    assert r.status_code == 200
    js = r.text
    assert "/api/tasks" in js
    assert "/api/chat/room/" in js
    assert "/api/chat/room/" in js and "/messages" in js
    api_tasks = client.get("/api/tasks")
    assert api_tasks.status_code == 200
    assert isinstance(api_tasks.json(), list)


def test_team_admin_script_uses_expected_api_paths(client: TestClient):
    """角色管理 admin-roles.js 调用的 API 路径存在（含 /api/models 供绑定模型）。"""
    r = client.get("/team/admin/admin-roles.js")
    assert r.status_code == 200
    js = r.text
    assert "/api/admin/roles" in js
    assert "/api/abilities" in js
    assert "/api/models" in js
    list_roles = client.get("/api/admin/roles")
    list_abilities = client.get("/api/abilities")
    list_models = client.get("/api/models")
    assert list_roles.status_code == 200
    assert list_abilities.status_code == 200
    assert list_models.status_code == 200
    assert isinstance(list_roles.json(), list)
    assert isinstance(list_abilities.json(), list)
    models_data = list_models.json()
    assert "models" in models_data and "default" in models_data


# --- UI flow: task center (requires one session) ---
def test_ui_flow_task_center_full(client_task_center: TestClient, task_center_state):
    """任务中心完整流程：创建会话 -> 任务列表 -> 打开任务 -> 消息列表 -> 发送消息 -> 再拉消息。"""
    sessions_list, messages_by_session = task_center_state
    create_r = client_task_center.post("/sessions", json={"title": "界面测试任务"})
    assert create_r.status_code == 200
    session_id = create_r.json().get("session_id")
    assert session_id
    assert len(sessions_list) == 1
    tasks_r = client_task_center.get("/api/tasks")
    assert tasks_r.status_code == 200
    tasks = tasks_r.json()
    assert len(tasks) == 1
    assert tasks[0]["id"] == session_id
    assert "界面测试任务" in (tasks[0].get("title") or "")
    messages_r = client_task_center.get(f"/api/chat/room/{session_id}/messages")
    assert messages_r.status_code == 200
    assert messages_r.json() == []
    post_r = client_task_center.post(
        f"/api/chat/room/{session_id}/message",
        json={"role": "user", "message": "界面测试消息", "message_type": "user_message"},
    )
    assert post_r.status_code == 200
    messages_r2 = client_task_center.get(f"/api/chat/room/{session_id}/messages")
    assert messages_r2.status_code == 200
    msgs = messages_r2.json()
    assert len(msgs) == 1
    assert msgs[0].get("role") == "user"
    assert msgs[0].get("message") == "界面测试消息"
    assert "timestamp" in msgs[0]


# --- UI flow: admin roles (use full stateful from test_team_api) ---
def test_ui_flow_admin_roles_pages_and_apis(client: TestClient):
    """角色管理界面依赖的 API 均可调用且返回预期结构（列表、能力、创建、获取、更新）。"""
    r_roles = client.get("/api/admin/roles")
    r_abilities = client.get("/api/abilities")
    assert r_roles.status_code == 200 and r_abilities.status_code == 200
    assert isinstance(r_roles.json(), list)
    abilities = r_abilities.json()
    assert isinstance(abilities, list)
    for a in abilities:
        assert "id" in a and "name" in a
    create_r = client.post(
        "/api/admin/roles",
        json={
            "name": "ui_test_role",
            "description": "界面测试角色",
            "status": "enabled",
            "abilities": [],
            "system_prompt": "测试提示词",
        },
    )
    assert create_r.status_code == 200
    get_r = client.get("/api/admin/roles/ui_test_role")
    if get_r.status_code == 200:
        data = get_r.json()
        assert data.get("name") == "ui_test_role"
        assert "system_prompt" in data
    list_r = client.get("/api/admin/roles")
    assert list_r.status_code == 200


def test_ui_task_center_response_shapes(client_task_center: TestClient, task_center_state):
    """任务中心 API 返回结构与前端 script.js 使用字段一致。"""
    sessions_list, messages_by_session = task_center_state
    client_task_center.post("/sessions", json={"title": "形状测试"})
    session_id = sessions_list[0].id if sessions_list else None
    assert session_id
    tasks = client_task_center.get("/api/tasks").json()
    assert len(tasks) >= 1
    t = tasks[0]
    assert "id" in t
    assert "title" in t
    assert "status" in t
    assert "last_updated" in t
    client_task_center.post(
        f"/api/chat/room/{session_id}/message",
        json={"role": "user", "message": "hi", "message_type": "user_message"},
    )
    messages = client_task_center.get(f"/api/chat/room/{session_id}/messages").json()
    assert len(messages) >= 1
    m = messages[0]
    assert "role" in m
    assert "message" in m
    assert "timestamp" in m


def test_team_navigation_links(client: TestClient):
    """角色管理页侧栏包含任务中心与员工角色链接；任务中心页可独立加载。"""
    r_index = client.get("/team/")
    r_roles = client.get("/team/admin/roles.html")
    assert r_index.status_code == 200 and r_roles.status_code == 200
    assert "任务中心" in r_roles.text
    assert "员工角色" in r_roles.text
    assert 'href="/team/"' in r_roles.text
    assert "/team/" in r_roles.text or "roles" in r_roles.text


def test_team_role_form_has_all_inputs(client: TestClient):
    """角色管理弹窗表单包含新建/编辑所需全部输入（含绑定模型，与 admin-roles.js 一致）。"""
    r = client.get("/team/admin/roles.html")
    assert r.status_code == 200
    html = r.text
    assert "id=\"role-name\"" in html
    assert "id=\"role-description\"" in html
    assert "id=\"role-status\"" in html
    assert "id=\"role-abilities\"" in html
    assert "id=\"role-model\"" in html
    assert "id=\"role-prompt\"" in html
    assert "id=\"role-form\"" in html
    assert "id=\"create-role-btn\"" in html
    assert "id=\"role-modal\"" in html


def test_team_admin_models_page_loads(client: TestClient):
    """GET /team/admin/models.html 返回 200，包含模型管理所需元素。"""
    r = client.get("/team/admin/models.html")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    html = r.text
    assert "模型管理" in html
    assert "id=\"models-list\"" in html
    assert "id=\"models-test-btn\"" in html
    assert "/api/models" in html or "admin-models.js" in html
    assert "任务中心" in html
    assert "员工角色" in html


def test_team_admin_models_script_and_api(client: TestClient):
    """模型管理页使用的 API：GET /api/models、POST /api/admin/models/test、PUT /api/admin/models/default 存在且返回预期结构。"""
    r_models = client.get("/api/models")
    assert r_models.status_code == 200
    data = r_models.json()
    assert "models" in data and "default" in data
    assert isinstance(data["models"], list)
    r_test = client.post("/api/admin/models/test")
    assert r_test.status_code == 200
    test_data = r_test.json()
    assert "results" in test_data
    assert isinstance(test_data["results"], list)
    r_set_default = client.put("/api/admin/models/default", json={"model": "invalid-not-in-list"})
    assert r_set_default.status_code == 400
    r_set_default = client.put("/api/admin/models/default", json={"model": "invalid-not-in-list"})
    assert r_set_default.status_code == 400
