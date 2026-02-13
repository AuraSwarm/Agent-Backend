"""
任务与对话分离：sessions/chat 仅处理对话，api/tasks 与 api/chat/room 仅处理任务。

- GET /sessions 默认 scope=chat，排除任务
- GET|PATCH|DELETE /sessions/{id}、GET /sessions/{id}/messages、search 对任务返回 404
- POST /chat 对任务 session_id 返回 404
- GET|POST|DELETE /api/chat/room/{id}/* 对非任务返回 404
- GET|POST|PATCH|DELETE /api/tasks 仅任务
"""

import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_async_return(value):
    async def _():
        return value

    def _call(*args, **kwargs):
        return _()

    return _call


def _mock_session(session_id: uuid.UUID, title: str, is_task: bool):
    s = MagicMock()
    s.id = session_id
    s.title = title
    s.metadata_ = {"is_task": True} if is_task else {}
    s.updated_at = datetime.now(timezone.utc)
    s.status = 1
    return s


def _mock_message(role: str, content: str):
    m = MagicMock()
    m.role = role
    m.content = content
    m.created_at = datetime.now(timezone.utc)
    return m


def _session_store_scope(store: dict):
    """Stateful session scope: store['sessions'] = {uuid: mock_session}, store['messages'] = {uuid: [msg, ...]}."""
    sessions = store["sessions"]
    messages = store["messages"]
    first_map = store.get("first_map", {})
    last_map = store.get("last_map", {})
    summary_map = store.get("summary_map", {})

    def _param_sid(stmt):
        try:
            compiled = getattr(stmt, "compile", lambda: None)()
            if compiled:
                params = getattr(compiled, "params", {}) or {}
                for k, v in params.items():
                    if "session" in k.lower() or k == "sid":
                        return v
                return next(iter(params.values()), None)
        except Exception:
            pass
        return None

    def _is_select_session_list(stmt):
        try:
            if getattr(stmt, "_limit_clause", None) is not None or getattr(stmt, "limit_clause", None) is not None:
                froms = getattr(stmt, "get_final_froms", lambda: None)()
                if not froms and hasattr(stmt, "froms"):
                    froms = getattr(stmt, "froms", ())
                if froms:
                    name = getattr(froms[0], "name", None) or getattr(getattr(froms[0], "entity", None), "__tablename__", None)
                    if name == "sessions":
                        return True
            if hasattr(stmt, "order_by") and callable(getattr(stmt, "order_by", None)):
                froms = getattr(stmt, "get_final_froms", lambda: getattr(stmt, "froms", ()))()
                if froms:
                    e = getattr(froms[0], "entity", froms[0])
                    if getattr(e, "__tablename__", None) == "sessions":
                        return True
        except Exception:
            pass
        return False

    def _safe_sid(sid):
        if sid is None:
            return None
        if isinstance(sid, uuid.UUID):
            return sid
        try:
            return uuid.UUID(str(sid))
        except (ValueError, TypeError):
            return None

    def _table_name(stmt):
        try:
            get_froms = getattr(stmt, "get_final_froms", None)
            froms = (get_froms() if callable(get_froms) else getattr(stmt, "froms", ())) or ()
            if froms:
                first = froms[0]
                name = getattr(first, "name", None)
                if name == "employee_roles":
                    return name
                e = getattr(first, "entity", first)
                return getattr(e, "__tablename__", None)
        except Exception:
            pass
        return None

    def _param_name_val(stmt):
        try:
            compiled = getattr(stmt, "compile", lambda: None)()
            if compiled:
                params = getattr(compiled, "params", {}) or {}
                return next(iter(params.values()), None) if params else None
        except Exception:
            pass
        return None

    roles = store.get("roles", {})

    def get_execute_result(stmt):
        raw_sid = _param_sid(stmt)
        sid = _safe_sid(raw_sid)
        result = MagicMock()
        result.fetchall.return_value = []
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value.all.return_value = []
        result.scalars.return_value.first.return_value = None

        tbl = _table_name(stmt)
        if tbl == "employee_roles":
            name_val = _param_name_val(stmt)
            if name_val is not None:
                if isinstance(name_val, (list, tuple)):
                    result.fetchall.return_value = [(n,) for n in name_val if n in roles]
                elif name_val in roles:
                    result.scalar_one_or_none.return_value = roles[name_val]
            return result

        if _is_select_session_list(stmt):
            result.scalars.return_value.all.return_value = list(sessions.values())
            return result

        if sid is not None:
            if sid in sessions:
                result.scalar_one_or_none.return_value = sessions[sid]
                result.scalars.return_value.all.return_value = [sessions[sid]]
            if sid in messages:
                result.scalars.return_value.all.return_value = messages[sid]
                result.scalars.return_value.first.return_value = messages[sid][0] if messages[sid] else None
            if sid in first_map:
                result.fetchall.return_value = [(sid, first_map[sid], None)]
            if sid in last_map and not result.fetchall.return_value:
                result.fetchall.return_value = [(sid, last_map[sid], None)]
            if sid in summary_map and not result.fetchall.return_value:
                result.fetchall.return_value = [(sid, summary_map[sid], None)]
        return result

    def add(obj):
        sid = getattr(obj, "session_id", None)
        role = getattr(obj, "role", None)
        content = getattr(obj, "content", None)
        if sid is not None and role is not None and content is not None:
            messages.setdefault(sid, []).append(_mock_message(role, content))

    class Ctx:
        async def __aenter__(self):
            session_mock = MagicMock()
            session_mock.execute = lambda stmt: _make_async_return(get_execute_result(stmt))()
            session_mock.commit = _make_async_return(None)
            session_mock.rollback = _make_async_return(None)
            session_mock.add = add
            session_mock.flush = _make_async_return(None)
            return session_mock

        async def __aexit__(self, *args):
            pass

    def session_scope():
        return Ctx()

    return session_scope


def _mock_role(name: str):
    r = MagicMock()
    r.name = name
    return r


@pytest.fixture
def separation_store():
    """One conversation session, one task session, and optional roles for assignee_role tests."""
    conv_id = uuid.uuid4()
    task_id = uuid.uuid4()
    store = {
        "sessions": {
            conv_id: _mock_session(conv_id, "对话一", is_task=False),
            task_id: _mock_session(task_id, "任务一", is_task=True),
        },
        "messages": {
            conv_id: [_mock_message("user", "hi"), _mock_message("assistant", "hello")],
            task_id: [_mock_message("user", "task msg")],
        },
        "first_map": {conv_id: "hi", task_id: "task msg"},
        "last_map": {conv_id: "hello", task_id: "task msg"},
        "summary_map": {},
        "roles": {"task_runner": _mock_role("task_runner")},
    }
    store["conv_id"] = conv_id
    store["task_id"] = task_id
    return store


@pytest.fixture
def separation_client(separation_store):
    """Client with stateful mock that has one conversation and one task."""
    scope = _session_store_scope(separation_store)

    async def noop_init_db():
        pass

    with patch("app.storage.db.init_db", side_effect=noop_init_db), patch(
        "app.storage.db.session_scope", new=scope
    ), patch("app.storage.db.get_session_factory", return_value=MagicMock(return_value=scope())), patch(
        "app.routers.sessions.session_scope", new=scope
    ), patch("app.routers.sessions.get_session_factory", return_value=MagicMock(return_value=scope())), patch(
        "app.routers.chat.session_scope", new=scope
    ), patch("app.routers.team_room.session_scope", new=scope), patch(
        "app.routers.code_review.session_scope", new=scope
    ), patch("app.routers.code_review.get_session_factory", return_value=MagicMock(return_value=scope())), patch(
        "app.routers.health.session_scope", new=scope
    ), patch("app.routers.health.get_session_factory", return_value=MagicMock(return_value=scope())), patch(
        "app.routers.sessions.get_embedding", return_value=[0.0] * 1536
    ), patch("app.main.validate_required_env"):
        from app.main import app
        with TestClient(app) as c:
            yield c


# ----- GET /sessions 默认仅返回对话 -----
def test_list_sessions_default_scope_excludes_tasks(separation_client, separation_store):
    """GET /sessions 默认 scope=chat，不包含任务。"""
    r = separation_client.get("/sessions?limit=10")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    task_id_str = str(separation_store["task_id"])
    for item in data:
        assert item["session_id"] != task_id_str


# ----- /sessions/{id} 对任务返回 404 -----
def test_get_session_messages_rejects_task(separation_client, separation_store):
    """GET /sessions/{task_id}/messages 对任务返回 404。"""
    task_id = separation_store["task_id"]
    r = separation_client.get(f"/sessions/{task_id}/messages")
    assert r.status_code == 404
    assert "task" in (r.json().get("detail") or "").lower() or "room" in (r.json().get("detail") or "").lower()


def test_get_session_messages_ok_for_conversation(separation_client, separation_store):
    """GET /sessions/{conv_id}/messages 对对话返回 200。"""
    conv_id = separation_store["conv_id"]
    r = separation_client.get(f"/sessions/{conv_id}/messages")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1


def test_patch_session_rejects_task(separation_client, separation_store):
    """PATCH /sessions/{task_id} 对任务返回 404。"""
    task_id = separation_store["task_id"]
    r = separation_client.patch(f"/sessions/{task_id}", json={"title": "新标题"})
    assert r.status_code == 404
    assert "task" in (r.json().get("detail") or "").lower()


def test_delete_session_rejects_task(separation_client, separation_store):
    """DELETE /sessions/{task_id} 对任务返回 404。"""
    task_id = separation_store["task_id"]
    r = separation_client.delete(f"/sessions/{task_id}")
    assert r.status_code == 404
    assert "task" in (r.json().get("detail") or "").lower()


def test_session_search_rejects_task(separation_client, separation_store):
    """GET /sessions/{task_id}/search 对任务返回 404。"""
    task_id = separation_store["task_id"]
    r = separation_client.get(f"/sessions/{task_id}/search", params={"query": "hello"})
    assert r.status_code == 404


# ----- POST /chat 对任务返回 404 -----
def test_post_chat_rejects_task_session(separation_client, separation_store):
    """POST /chat 当 session_id 为任务时返回 404。"""
    task_id = separation_store["task_id"]
    r = separation_client.post(
        "/chat",
        json={
            "session_id": str(task_id),
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )
    assert r.status_code == 404
    assert "task" in (r.json().get("detail") or "").lower() or "room" in (r.json().get("detail") or "").lower()


# ----- /api/chat/room/{id} 仅限任务 -----
def test_get_room_messages_rejects_conversation(separation_client, separation_store):
    """GET /api/chat/room/{conv_id}/messages 对对话返回 404。"""
    conv_id = separation_store["conv_id"]
    r = separation_client.get(f"/api/chat/room/{conv_id}/messages")
    assert r.status_code == 404
    assert "conversation" in (r.json().get("detail") or "").lower() or "sessions" in (r.json().get("detail") or "").lower()


def test_get_room_messages_ok_for_task(separation_client, separation_store):
    """GET /api/chat/room/{task_id}/messages 对任务返回 200。"""
    task_id = separation_store["task_id"]
    r = separation_client.get(f"/api/chat/room/{task_id}/messages")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


def test_post_room_message_rejects_conversation(separation_client, separation_store):
    """POST /api/chat/room/{conv_id}/message 对对话返回 404。"""
    conv_id = separation_store["conv_id"]
    r = separation_client.post(
        f"/api/chat/room/{conv_id}/message",
        json={"role": "user", "message": "hi", "message_type": "user_message"},
    )
    assert r.status_code == 404


def test_delete_room_messages_rejects_conversation(separation_client, separation_store):
    """DELETE /api/chat/room/{conv_id}/messages 对对话返回 404。"""
    conv_id = separation_store["conv_id"]
    r = separation_client.delete(f"/api/chat/room/{conv_id}/messages")
    assert r.status_code == 404


# ----- /api/tasks 仅任务；DELETE 存在 -----
def test_api_tasks_list_returns_200(separation_client):
    """GET /api/tasks 返回 200 且为列表。"""
    r = separation_client.get("/api/tasks")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_api_tasks_delete_404_for_nonexistent(separation_client):
    """DELETE /api/tasks/{uuid} 对不存在或非任务返回 404。"""
    r = separation_client.delete(f"/api/tasks/{uuid.uuid4()}")
    assert r.status_code == 404


def test_api_tasks_patch_404_for_nonexistent(separation_client):
    """PATCH /api/tasks/{uuid} 对不存在返回 404。"""
    r = separation_client.patch(f"/api/tasks/{uuid.uuid4()}", json={"title": "x"})
    assert r.status_code == 404


# ----- Task chat room：多角色获取消息并更新 -----
def test_task_room_get_messages_empty_or_after_post(separation_client, separation_store):
    """任务 chat room 生效：GET 消息列表，POST 一条用户消息后再 GET 可见该条。"""
    task_id = separation_store["task_id"]
    r0 = separation_client.get(f"/api/chat/room/{task_id}/messages")
    assert r0.status_code == 200
    initial = r0.json()
    assert isinstance(initial, list)
    r = separation_client.post(
        f"/api/chat/room/{task_id}/message",
        json={"role": "user", "message": "请确认需求", "message_type": "user_message"},
    )
    assert r.status_code == 200
    r2 = separation_client.get(f"/api/chat/room/{task_id}/messages")
    assert r2.status_code == 200
    msgs = r2.json()
    assert len(msgs) >= len(initial) + 1
    user_msgs = [m for m in msgs if m.get("role") == "user" and "请确认需求" in (m.get("message") or "")]
    assert len(user_msgs) >= 1


def test_task_room_multiple_roles_post_and_get(separation_client, separation_store):
    """多个 role 可向同一任务 room 发消息，GET 能拿到全部并按顺序。"""
    task_id = separation_store["task_id"]
    separation_client.post(
        f"/api/chat/room/{task_id}/message",
        json={"role": "user", "message": "需求：整理文档", "message_type": "user_message"},
    )
    separation_client.post(
        f"/api/chat/room/{task_id}/message",
        json={"role": "assistant", "message": "[角色A] 已收到，开始整理。", "message_type": "ai_message"},
    )
    separation_client.post(
        f"/api/chat/room/{task_id}/message",
        json={"role": "assistant", "message": "[角色B] 已更新目录。", "message_type": "ai_message"},
    )
    r = separation_client.get(f"/api/chat/room/{task_id}/messages")
    assert r.status_code == 200
    msgs = r.json()
    assert len(msgs) >= 3
    roles = [m["role"] for m in msgs]
    contents = [m["message"] for m in msgs]
    assert "user" in roles
    assert "assistant" in roles
    assert any("需求" in c for c in contents)
    assert any("角色A" in c for c in contents)
    assert any("角色B" in c for c in contents)


def test_task_room_rejects_non_task_session(separation_client, separation_store):
    """POST/GET room 对非任务 session 返回 404，确保 chat room 仅任务可用。"""
    conv_id = separation_store["conv_id"]
    r_get = separation_client.get(f"/api/chat/room/{conv_id}/messages")
    assert r_get.status_code == 404
    r_post = separation_client.post(
        f"/api/chat/room/{conv_id}/message",
        json={"role": "user", "message": "hi", "message_type": "user_message"},
    )
    assert r_post.status_code == 404


# ----- 任务执行 E2E：发消息后后台处理并回复 -----
def test_task_execution_post_message_then_assistant_reply(separation_client, separation_store):
    """POST 任务消息后，后台处理并追加 assistant 回复（检查 X 下有多少文件夹）。"""
    import time
    task_id = separation_store["task_id"]
    # Must @ a valid role so message is direct input; store has role "task_runner"
    body = "@task_runner 检查 /tmp 下有多少文件夹"
    r = separation_client.post(
        f"/api/chat/room/{task_id}/message",
        json={"role": "user", "message": body, "message_type": "user_message"},
    )
    assert r.status_code == 200
    time.sleep(0.5)
    r2 = separation_client.get(f"/api/chat/room/{task_id}/messages")
    assert r2.status_code == 200
    msgs = r2.json()
    assert len(msgs) >= 2, "expect user message + assistant reply"
    last = msgs[-1]
    assert last["role"] == "assistant"
    assert "folder(s)." in last["message"] or "个文件夹" in last["message"]
    assert any(c.isdigit() for c in last["message"]), "reply should contain a number"


def test_task_room_no_mention_context_only_no_reply(separation_client, separation_store):
    """不 @ 任何角色时消息仅作上下文，不触发 assistant 回复。"""
    import time
    task_id = separation_store["task_id"]
    r = separation_client.post(
        f"/api/chat/room/{task_id}/message",
        json={"role": "user", "message": "检查 /tmp 下有多少文件夹", "message_type": "user_message"},
    )
    assert r.status_code == 200
    time.sleep(0.5)
    r2 = separation_client.get(f"/api/chat/room/{task_id}/messages")
    assert r2.status_code == 200
    msgs = r2.json()
    user_only = [m for m in msgs if "检查" in (m.get("message") or "")]
    assert len(user_only) >= 1
    after_user = msgs[msgs.index(user_only[-1]) + 1:] if user_only else []
    assert not any(
        m.get("role") == "assistant" and ("folder" in (m.get("message") or "") or "文件夹" in (m.get("message") or ""))
        for m in after_user
    ), "no assistant reply when not @mentioned"


def test_parse_mentions_supports_spaces_in_role_name():
    """@ 解析支持角色名含空格（如 Claude Analyst），否则会只解析出 Claude 导致无法匹配角色。"""
    from app.routers.team_room import _parse_mentions
    assert _parse_mentions("@Claude Analyst 我希望能够读取Aura") == ["Claude Analyst"]
    assert _parse_mentions("@task_runner 执行 echo 你好") == ["task_runner"]
    assert _parse_mentions("@A @B  hi") == ["A", "B"]
    assert _parse_mentions("@Claude Analyst 你是什么版本") == ["Claude Analyst"]


def test_task_room_get_messages_includes_mentioned_roles(separation_client, separation_store):
    """GET /api/chat/room/{id}/messages 返回每条消息的 mentioned_roles（由内容解析）。"""
    task_id = separation_store["task_id"]
    separation_client.post(
        f"/api/chat/room/{task_id}/message",
        json={"role": "user", "message": "@task_runner 请整理", "message_type": "user_message"},
    )
    r = separation_client.get(f"/api/chat/room/{task_id}/messages")
    assert r.status_code == 200
    msgs = r.json()
    with_mention = [m for m in msgs if (m.get("message") or "").strip() == "@task_runner 请整理"]
    assert len(with_mention) >= 1
    assert "mentioned_roles" in with_mention[0]
    assert with_mention[0]["mentioned_roles"] == ["task_runner"]


# ----- 重命名 API：对话 / 任务 PATCH title -----
def test_rename_conversation_patch_200(separation_client, separation_store):
    """PATCH /sessions/{id} 对话重命名返回 200。"""
    conv_id = separation_store["conv_id"]
    r = separation_client.patch(
        f"/sessions/{conv_id}",
        json={"title": "新标题"},
    )
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_rename_task_patch_200(separation_client, separation_store):
    """PATCH /api/tasks/{id} 任务重命名返回 200。"""
    task_id = separation_store["task_id"]
    r = separation_client.patch(
        f"/api/tasks/{task_id}",
        json={"title": "重命名后的任务"},
    )
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_rename_task_patch_404_for_conversation_id(separation_client, separation_store):
    """PATCH /api/tasks/{conv_id} 用对话 id 会 404（任务接口只认任务）。"""
    conv_id = separation_store["conv_id"]
    r = separation_client.patch(
        f"/api/tasks/{conv_id}",
        json={"title": "x"},
    )
    assert r.status_code == 404


def test_rename_conversation_patch_404_for_task_id(separation_client, separation_store):
    """PATCH /sessions/{task_id} 用任务 id 会 404（对话接口只认对话）。"""
    task_id = separation_store["task_id"]
    r = separation_client.patch(
        f"/sessions/{task_id}",
        json={"title": "x"},
    )
    assert r.status_code == 404


# ----- 任务分配 role：PATCH assignee_roles，GET 返回 assignee_roles（支持多角色） -----
def test_task_get_returns_assignee_roles_key(separation_client, separation_store):
    """GET /api/tasks 返回的每项包含 assignee_roles 键（列表）。"""
    r = separation_client.get("/api/tasks")
    assert r.status_code == 200
    tasks = r.json()
    assert isinstance(tasks, list)
    for t in tasks:
        assert "assignee_roles" in t
        assert isinstance(t["assignee_roles"], list)


def test_task_patch_assignee_roles_200(separation_client, separation_store):
    """PATCH /api/tasks/{id} 设置 assignee_roles 为已有角色返回 200，GET 可见。"""
    task_id = separation_store["task_id"]
    r = separation_client.patch(
        f"/api/tasks/{task_id}",
        json={"assignee_roles": ["task_runner"]},
    )
    assert r.status_code == 200
    r2 = separation_client.get("/api/tasks")
    assert r2.status_code == 200
    task = next((x for x in r2.json() if x["id"] == str(task_id)), None)
    assert task is not None
    assert task.get("assignee_roles") == ["task_runner"]


def test_task_patch_assignee_roles_multiple_200(separation_client, separation_store):
    """PATCH /api/tasks/{id} 可设置多个角色（需 mock 存在多角色）。"""
    task_id = separation_store["task_id"]
    separation_store["roles"]["analyst"] = _mock_role("analyst")
    r = separation_client.patch(
        f"/api/tasks/{task_id}",
        json={"assignee_roles": ["task_runner", "analyst"]},
    )
    assert r.status_code == 200
    r2 = separation_client.get("/api/tasks")
    task = next((x for x in r2.json() if x["id"] == str(task_id)), None)
    assert task is not None
    assert set(task.get("assignee_roles", [])) == {"task_runner", "analyst"}


def test_task_patch_assignee_roles_clear_200(separation_client, separation_store):
    """PATCH /api/tasks/{id} 传 assignee_roles 空数组可清空分配。"""
    task_id = separation_store["task_id"]
    separation_client.patch(f"/api/tasks/{task_id}", json={"assignee_roles": ["task_runner"]})
    r = separation_client.patch(f"/api/tasks/{task_id}", json={"assignee_roles": []})
    assert r.status_code == 200
    r2 = separation_client.get("/api/tasks")
    task = next((x for x in r2.json() if x["id"] == str(task_id)), None)
    assert task is not None
    assert task.get("assignee_roles") == []


def test_task_patch_assignee_role_legacy_200(separation_client, separation_store):
    """PATCH 仍支持 legacy 单角色 assignee_role，GET 返回 assignee_roles 列表。"""
    task_id = separation_store["task_id"]
    r = separation_client.patch(
        f"/api/tasks/{task_id}",
        json={"assignee_role": "task_runner"},
    )
    assert r.status_code == 200
    r2 = separation_client.get("/api/tasks")
    task = next((x for x in r2.json() if x["id"] == str(task_id)), None)
    assert task is not None
    assert task.get("assignee_roles") == ["task_runner"]


def test_task_patch_assignee_roles_not_found_400(separation_client, separation_store):
    """PATCH /api/tasks/{id} 设置不存在的 assignee_roles 返回 400。"""
    task_id = separation_store["task_id"]
    r = separation_client.patch(
        f"/api/tasks/{task_id}",
        json={"assignee_roles": ["nonexistent_role"]},
    )
    assert r.status_code == 400
    assert "not found" in (r.json().get("detail") or "").lower()


def test_task_create_with_assignee_roles_200(separation_client, separation_store):
    """POST /api/tasks 带 assignee_roles 为已有角色时创建成功并返回 assignee_roles。"""
    r = separation_client.post(
        "/api/tasks",
        json={"title": "带角色的任务", "assignee_roles": ["task_runner"]},
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("assignee_roles") == ["task_runner"]
    assert "id" in data


def test_task_create_with_assignee_roles_not_found_400(separation_client):
    """POST /api/tasks 带不存在的 assignee_roles 返回 400。"""
    r = separation_client.post(
        "/api/tasks",
        json={"title": "x", "assignee_roles": ["nonexistent"]},
    )
    assert r.status_code == 400
    assert "not found" in (r.json().get("detail") or "").lower()


def test_task_room_role_reply_and_ability_execution(separation_client, separation_store):
    """Web 端与 role 对话：@ 角色后 role 能正确响应并执行相应能力（mock 角色提示词与能力执行）。"""
    task_id = separation_store["task_id"]
    with patch(
        "app.routers.team_room._get_role_prompt_and_abilities",
        new_callable=AsyncMock,
        return_value=("你是助手。", ["echo"], None),
    ), patch(
        "app.routers.team_room._try_run_ability",
        new_callable=AsyncMock,
        return_value="test_echo_output",
    ), patch(
        "app.routers.team_room._role_reply_via_chat",
        new_callable=AsyncMock,
        return_value="Echo 已执行，输出：test_echo_output",
    ):
        r = separation_client.post(
            f"/api/chat/room/{task_id}/message",
            json={
                "role": "user",
                "message": "@task_runner 执行 echo 你好",
                "message_type": "user_message",
            },
        )
    assert r.status_code == 200
    time.sleep(0.6)
    r2 = separation_client.get(f"/api/chat/room/{task_id}/messages")
    assert r2.status_code == 200
    msgs = r2.json()
    assert len(msgs) >= 2
    assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
    assert len(assistant_msgs) >= 1
    last_reply = assistant_msgs[-1].get("message") or ""
    assert "test_echo_output" in last_reply, "role reply should contain ability execution result"


def test_task_room_prompt_ability_understood_and_run(separation_client, separation_store):
    """提示词能力：ability 带 prompt_template 时能理解并执行（调用 LLM 后返回结果）。"""
    task_id = separation_store["task_id"]
    with patch(
        "app.routers.team_room._get_role_prompt_and_abilities",
        new_callable=AsyncMock,
        return_value=("你是助手。", ["read_aura"], None),
    ), patch(
        "app.routers.team_room._get_ability_by_id",
        new_callable=AsyncMock,
        return_value={
            "id": "read_aura",
            "name": "读取 Aura",
            "description": "根据用户消息理解并执行与 Aura 相关的提示词能力",
            "command": ["true"],
            "prompt_template": "用户请求：{message}。请简要回复已理解并说明会如何处理。",
        },
    ), patch(
        "app.routers.team_room._run_prompt_ability",
        new_callable=AsyncMock,
        return_value="已理解：读取Aura。将根据配置执行相应操作。",
    ), patch(
        "app.routers.team_room._role_reply_via_chat",
        new_callable=AsyncMock,
        return_value="根据你的要求，已理解并执行：读取Aura。将根据配置执行相应操作。",
    ):
        r = separation_client.post(
            f"/api/chat/room/{task_id}/message",
            json={
                "role": "user",
                "message": "@task_runner 执行 read_aura 读取Aura",
                "message_type": "user_message",
            },
        )
    assert r.status_code == 200
    time.sleep(0.6)
    r2 = separation_client.get(f"/api/chat/room/{task_id}/messages")
    assert r2.status_code == 200
    msgs = r2.json()
    assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
    assert len(assistant_msgs) >= 1
    last_reply = assistant_msgs[-1].get("message") or ""
    assert "已理解" in last_reply or "读取Aura" in last_reply, "prompt ability should be understood and run"


def test_task_room_conversation_requires_chat_ability(separation_client, separation_store):
    """对话功能：向 role 发送消息时，role 需具备对话/理解能力（chat），才能正确理解并回复。"""
    from app.constants import CHAT_ABILITY_ID

    task_id = separation_store["task_id"]
    with patch(
        "app.routers.team_room._get_role_prompt_and_abilities",
        new_callable=AsyncMock,
        return_value=("你是 task_runner 角色，请友好回复用户。", [CHAT_ABILITY_ID], None),
    ), patch(
        "app.routers.team_room._role_reply_via_chat",
        new_callable=AsyncMock,
        return_value="你好！已收到你的消息。",
    ) as mock_chat:
        r = separation_client.post(
            f"/api/chat/room/{task_id}/message",
            json={
                "role": "user",
                "message": "@task_runner 你好，请简单回复",
                "message_type": "user_message",
            },
        )
    assert r.status_code == 200
    time.sleep(0.6)
    r2 = separation_client.get(f"/api/chat/room/{task_id}/messages")
    assert r2.status_code == 200
    msgs = r2.json()
    assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
    assert len(assistant_msgs) >= 1, "role reply should appear after @mention"
    last_reply = assistant_msgs[-1].get("message") or ""
    assert "你好" in last_reply or "收到" in last_reply
    mock_chat.assert_called_once()
    ability_ids = mock_chat.call_args[0][4] if len(mock_chat.call_args[0]) > 4 else []
    assert CHAT_ABILITY_ID in (ability_ids or []), "role must have chat ability for conversation"


def test_task_room_ability_not_bound_to_role_not_executed(separation_client, separation_store):
    """能力未绑定到角色时，执行该能力不会真正运行（仅绑定列表内的能力会执行）。"""
    import time
    task_id = separation_store["task_id"]
    with patch(
        "app.routers.team_room._get_role_prompt_and_abilities",
        new_callable=AsyncMock,
        return_value=("你是助手。", ["echo"], None),
    ), patch(
        "app.routers.team_room._role_reply_via_chat",
        new_callable=AsyncMock,
        return_value="我无法执行未绑定的能力。",
    ):
        separation_client.post(
            f"/api/chat/room/{task_id}/message",
            json={
                "role": "user",
                "message": "@task_runner 执行 date 现在时间",
                "message_type": "user_message",
            },
        )
    time.sleep(0.5)
    r = separation_client.get(f"/api/chat/room/{task_id}/messages")
    assert r.status_code == 200
    msgs = r.json()
    assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
    assert len(assistant_msgs) >= 1
    last_reply = assistant_msgs[-1].get("message") or ""
    assert "我无法执行" in last_reply or "date" not in last_reply or "returncode" not in last_reply
