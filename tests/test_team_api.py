"""Tests for AI 员工团队 API: /api/tasks, /api/admin/roles, /api/abilities, /api/chat/room/*."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from memory_base.models_team import EmployeeRole, PromptVersion, RoleAbility

from app.storage.models import CustomAbility


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
    Session scope for full CRUD: roles, abilities, prompts, custom_abilities.
    store = {"roles": {}, "abilities": {}, "prompts": {}, "custom_abilities": {}}
    """
    roles = store["roles"]
    abilities = store["abilities"]
    prompts = store["prompts"]
    custom_abilities = store.setdefault("custom_abilities", {})

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

            if table_name == "custom_abilities":
                ab_id = _param_value(stmt)
                result = MagicMock()
                if ab_id is not None:
                    row = custom_abilities.get(ab_id)
                    result.scalar_one_or_none.return_value = row
                    result.scalars.return_value.all.return_value = [row] if row else []
                else:
                    result.scalar_one_or_none.return_value = None
                    result.scalars.return_value.all.return_value = list(custom_abilities.values())
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
            roles[obj.name] = obj
        elif type(obj) is RoleAbility:
            abilities.setdefault(obj.role_name, []).append(obj.ability_id)
        elif type(obj) is PromptVersion:
            prompts.setdefault(obj.role_name, []).append((obj.version, obj.content))
        elif type(obj) is CustomAbility:
            row = MagicMock()
            row.id = obj.id
            row.name = obj.name
            row.description = obj.description or ""
            row.command = getattr(obj, "command", [])
            row.prompt_template = getattr(obj, "prompt_template", None)
            custom_abilities[obj.id] = row

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


def test_api_tasks_create_independent(client):
    """POST /api/tasks 可独立创建任务，不依赖 chat；返回与 GET 单项相同结构。"""
    r = client.post("/api/tasks", json={})
    assert r.status_code == 200
    data = r.json()
    assert "id" in data
    assert "title" in data
    assert "status" in data
    assert "last_updated" in data
    assert data["title"] == "未命名任务"
    assert data["status"] in ("in_progress", "completed")
    assert isinstance(data["id"], str) and len(data["id"]) > 0

    r2 = client.post("/api/tasks", json={"title": " 独立任务标题 "})
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["title"] == "独立任务标题"
    assert isinstance(d2["id"], str) and len(d2["id"]) > 0


def test_api_tasks_delete_404(client):
    """DELETE /api/tasks/{id} 存在：非任务或不存在时返回 404。"""
    r = client.delete("/api/tasks/00000000-0000-0000-0000-000000000001")
    assert r.status_code == 404


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
    assert "chat" in data["abilities"] and "echo" in data["abilities"], "role has built-in chat + echo"
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
    assert set(data2["abilities"]) == {"chat", "echo", "date"}, "built-in chat + updated abilities"


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


def test_employee_binds_and_uses_multiple_abilities(client_full_stateful):
    """员工可绑定多种能力，且每种能力均可被正确执行（测试员工对能力的应用）。"""
    from app.constants import CHAT_ABILITY_ID

    ab_r = client_full_stateful.get("/api/abilities")
    assert ab_r.status_code == 200
    all_abilities = [a["id"] for a in ab_r.json()]
    assert len(all_abilities) >= 1, "config must expose at least one ability (e.g. echo, date)"
    executable = [a for a in all_abilities if a != CHAT_ABILITY_ID]
    role_abilities = executable[:2] if len(executable) >= 2 else executable

    role_name = "multi_ability_employee"
    create_r = client_full_stateful.post(
        "/api/admin/roles",
        json={
            "name": role_name,
            "description": "员工绑定多种能力",
            "status": "enabled",
            "abilities": role_abilities,
            "system_prompt": "",
        },
    )
    assert create_r.status_code == 200

    get_r = client_full_stateful.get(f"/api/admin/roles/{role_name}")
    assert get_r.status_code == 200
    bound = get_r.json().get("abilities") or []
    assert CHAT_ABILITY_ID in bound, "every role has built-in chat ability"
    assert set(bound) >= set(role_abilities), "role must expose bound abilities plus chat"

    for ability_id in bound:
        if ability_id == CHAT_ABILITY_ID:
            continue
        if ability_id == "echo":
            exec_r = client_full_stateful.post(
                "/tools/execute", json={"tool_id": ability_id, "params": {"message": "ok"}}
            )
        else:
            exec_r = client_full_stateful.post(
                "/tools/execute", json={"tool_id": ability_id, "params": {}}
            )
        assert exec_r.status_code == 200, f"execute {ability_id}: {exec_r.text}"
        assert exec_r.json().get("returncode") == 0, f"execute {ability_id} should succeed"


def test_api_admin_ensure_chat_ability(client_full_stateful):
    """历史角色适配：POST /api/admin/roles/ensure-chat-ability 可为未绑定对话能力的角色补齐能力。"""
    client_full_stateful.post(
        "/api/admin/roles",
        json={"name": "legacy_role", "description": "历史角色", "status": "enabled", "abilities": ["echo"], "system_prompt": ""},
    )
    r = client_full_stateful.post("/api/admin/roles/ensure-chat-ability")
    assert r.status_code == 200
    body = r.json()
    assert "updated" in body
    get_r = client_full_stateful.get("/api/admin/roles/legacy_role")
    assert get_r.status_code == 200
    abilities = get_r.json().get("abilities") or []
    assert "chat" in abilities, "role must have chat ability after ensure"


def test_api_admin_migrate_prompt_template(client):
    """Web 端修复按钮：POST /api/admin/migrate-prompt-template 执行单次迁移（幂等），返回 200。"""
    r = client.post("/api/admin/migrate-prompt-template")
    assert r.status_code == 200
    body = r.json()
    assert body.get("message") == "OK"
    assert "detail" in body or "message" in body


def test_api_abilities_list_has_schema(client):
    """GET /api/abilities 每项具备 id、name、description，custom 项含 source、command、prompt_template。"""
    r = client.get("/api/abilities")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    for item in data:
        assert "id" in item and isinstance(item["id"], str)
        assert "name" in item
        assert "description" in item
        if item.get("source") == "custom":
            assert "command" in item
            assert "prompt_template" in item


def test_api_abilities_create_with_prompt_template(client_full_stateful):
    """POST /api/abilities 可创建带 prompt_template 的提示词能力；GET 单条返回 prompt_template。"""
    body = {
        "id": "prompt_ability_test",
        "name": "提示词能力测试",
        "description": "根据用户消息执行 LLM 提示词",
        "command": ["true"],
        "prompt_template": "用户请求：{message}。请简要回复。",
    }
    r = client_full_stateful.post("/api/abilities", json=body)
    assert r.status_code == 200
    get_r = client_full_stateful.get("/api/abilities/prompt_ability_test")
    assert get_r.status_code == 200
    data = get_r.json()
    assert data["id"] == "prompt_ability_test"
    assert data["name"] == "提示词能力测试"
    assert data.get("source") == "custom"
    assert data.get("prompt_template") == "用户请求：{message}。请简要回复。"


def test_api_abilities_update_prompt_template(client_full_stateful):
    """PUT /api/abilities/{id} 可更新 prompt_template；GET 返回更新后的值。"""
    client_full_stateful.post(
        "/api/abilities",
        json={
            "id": "update_prompt_ab",
            "name": "可更新",
            "description": "测试更新",
            "command": ["true"],
            "prompt_template": "旧模板：{message}",
        },
    )
    r = client_full_stateful.put(
        "/api/abilities/update_prompt_ab",
        json={"prompt_template": "新模板：{message}"},
    )
    assert r.status_code == 200
    get_r = client_full_stateful.get("/api/abilities/update_prompt_ab")
    assert get_r.status_code == 200
    assert get_r.json().get("prompt_template") == "新模板：{message}"


def test_api_abilities_list_includes_custom_with_prompt_template(client_full_stateful):
    """创建带 prompt_template 的自定义能力后，GET /api/abilities 列表包含该能力且含 prompt_template。"""
    client_full_stateful.post(
        "/api/abilities",
        json={
            "id": "list_prompt_ab",
            "name": "列表提示词能力",
            "description": "用于列表测试",
            "command": ["true"],
            "prompt_template": "列表：{message}",
        },
    )
    r = client_full_stateful.get("/api/abilities")
    assert r.status_code == 200
    found = next((a for a in r.json() if a.get("id") == "list_prompt_ab"), None)
    assert found is not None
    assert found.get("prompt_template") == "列表：{message}"
    assert found.get("source") == "custom"


def test_api_models(client):
    """GET /api/models returns 200 and has models list and default (from config)."""
    r = client.get("/api/models")
    assert r.status_code == 200
    data = r.json()
    assert "models" in data
    assert "default" in data
    assert isinstance(data["models"], list)


def test_api_admin_models_test(client):
    """POST /api/admin/models/test returns 200 and results list with model_id, available, message."""
    r = client.post("/api/admin/models/test")
    assert r.status_code == 200
    data = r.json()
    assert "results" in data
    assert isinstance(data["results"], list)
    for item in data["results"]:
        assert "model_id" in item
        assert "available" in item
        assert "message" in item


def test_api_admin_models_set_default(tmp_path, monkeypatch, mock_db):
    """PUT /api/admin/models/default with valid model updates config and GET /api/models returns new default."""
    models_yaml = tmp_path / "models.yaml"
    models_yaml.write_text("""
embedding_providers:
  dashscope:
    api_key_env: "DASHSCOPE_API_KEY"
    endpoint: "https://example.com/embeddings"
    model: "text-embedding-v3"
    dimensions: 1536
    timeout: 10
default_embedding_provider: "dashscope"
chat_providers:
  dashscope:
    api_key_env: "DASHSCOPE_API_KEY"
    endpoint: "https://example.com/v1"
    model: "qwen-max"
    models:
      - qwen-max
      - qwen3-max
    timeout: 60
default_chat_provider: "dashscope"
summary_strategies: {}
""", encoding="utf-8")
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    from app.config import loader as config_loader
    config_loader._models_config = None
    with patch("app.main.validate_required_env"):
        from app.main import app
        with TestClient(app) as c:
            r = c.get("/api/models")
            assert r.status_code == 200
            assert r.json().get("default") == "qwen-max"
            put_r = c.put("/api/admin/models/default", json={"model": "qwen3-max"})
            assert put_r.status_code == 200
            assert put_r.json().get("default") == "qwen3-max"
            r2 = c.get("/api/models")
            assert r2.status_code == 200
            assert r2.json().get("default") == "qwen3-max"
    assert "qwen3-max" in (tmp_path / "models.yaml").read_text()


def test_api_admin_models_set_default_invalid_400(client):
    """PUT /api/admin/models/default with model not in allowed list returns 400."""
    r = client.put("/api/admin/models/default", json={"model": "not-in-list"})
    assert r.status_code == 400
    assert "default model" in (r.json().get("detail") or "").lower()


def test_api_admin_roles_create_with_default_model(client_full_stateful):
    """POST /api/admin/roles with valid default_model; GET returns it."""
    with patch("app.routers.team_admin._allowed_model_ids", return_value=["qwen-max", "qwen3-max"]):
        create_r = client_full_stateful.post(
            "/api/admin/roles",
            json={
                "name": "model_role",
                "description": "Role with model",
                "status": "enabled",
                "abilities": [],
                "system_prompt": "You are helpful.",
                "default_model": "qwen-max",
            },
        )
    assert create_r.status_code == 200
    get_r = client_full_stateful.get("/api/admin/roles/model_role")
    assert get_r.status_code == 200
    assert get_r.json().get("default_model") == "qwen-max"
    list_r = client_full_stateful.get("/api/admin/roles")
    assert list_r.status_code == 200
    role = next((x for x in list_r.json() if x["name"] == "model_role"), None)
    assert role is not None
    assert role.get("default_model") == "qwen-max"


def test_api_admin_roles_create_invalid_default_model_400(client_full_stateful):
    """POST /api/admin/roles with default_model not in allowed list returns 400."""
    with patch("app.routers.team_admin._allowed_model_ids", return_value=["only-allowed"]):
        r = client_full_stateful.post(
            "/api/admin/roles",
            json={
                "name": "bad_model_role",
                "description": "x",
                "status": "enabled",
                "abilities": [],
                "system_prompt": "x",
                "default_model": "not-in-list",
            },
        )
    assert r.status_code == 400
    assert "default_model" in (r.json().get("detail") or "").lower()


def test_api_admin_roles_update_default_model(client_full_stateful):
    """PUT /api/admin/roles with default_model; GET returns updated value."""
    with patch("app.routers.team_admin._allowed_model_ids", return_value=["qwen-max", "qwen3-max"]):
        client_full_stateful.post(
            "/api/admin/roles",
            json={
                "name": "update_model_role",
                "description": "Original",
                "status": "enabled",
                "abilities": [],
                "system_prompt": "Prompt.",
                "default_model": "qwen-max",
            },
        )
        put_r = client_full_stateful.put(
            "/api/admin/roles/update_model_role",
            json={"default_model": "qwen3-max"},
        )
    assert put_r.status_code == 200
    get_r = client_full_stateful.get("/api/admin/roles/update_model_role")
    assert get_r.status_code == 200
    assert get_r.json().get("default_model") == "qwen3-max"


def test_api_admin_roles_update_invalid_default_model_400(client_full_stateful):
    """PUT /api/admin/roles with default_model not in allowed list returns 400."""
    with patch("app.routers.team_admin._allowed_model_ids", return_value=["qwen-max"]):
        client_full_stateful.post(
            "/api/admin/roles",
            json={
                "name": "put_bad_role",
                "description": "x",
                "status": "enabled",
                "abilities": [],
                "system_prompt": "x",
            },
        )
        put_r = client_full_stateful.put(
            "/api/admin/roles/put_bad_role",
            json={"default_model": "invalid-model"},
        )
    assert put_r.status_code == 400
    assert "default_model" in (put_r.json().get("detail") or "").lower()
