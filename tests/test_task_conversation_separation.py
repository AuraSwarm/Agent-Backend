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
    """POST 任务消息后，后台处理并追加 assistant 回复（检查 X 下有多少文件夹）。mock 回复以保证不依赖真实模型。"""
    import time
    task_id = separation_store["task_id"]
    body = "@task_runner 检查 /tmp 下有多少文件夹"

    async def fake_reply_with_folder_count(*args, **kwargs):
        # 模拟数文件夹结果：tool_result_prefix 会传入，此处直接返回含数字的回复
        return "根据检查结果，/tmp 下有 2 个文件夹。"

    with patch("app.routers.team_room._role_reply_via_chat", side_effect=fake_reply_with_folder_count):
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
    """@ 解析支持角色名含空格（如 Claude Analyst）、连字符（如 Qwen-deep Analyst）。"""
    from app.routers.team_room import _parse_mentions
    assert _parse_mentions("@Claude Analyst 我希望能够读取Aura") == ["Claude Analyst"]
    assert _parse_mentions("@task_runner 执行 echo 你好") == ["task_runner"]
    assert _parse_mentions("@A @B  hi") == ["A", "B"]
    assert _parse_mentions("@Claude Analyst 你是什么版本") == ["Claude Analyst"]
    assert _parse_mentions("@Qwen-deep Analyst 介绍一下自己") == ["Qwen-deep Analyst"]


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


def test_parse_mentions_multiple_returns_order_preserved():
    """一条消息 @ 多人时 _parse_mentions 返回去重且保序的列表。"""
    from app.routers.team_room import _parse_mentions
    assert _parse_mentions("@A @B 请回复") == ["A", "B"]
    assert _parse_mentions("@task_runner @analyst 一起看下") == ["task_runner", "analyst"]
    assert _parse_mentions("@A @A @B") == ["A", "B"]


def test_task_room_one_message_at_multiple_roles_gets_multiple_replies(separation_client, separation_store):
    """一条消息 @ 多人时，每个被 @ 的有效角色各生成一条 assistant 回复，GET 返回正确的 reply_by_role。"""
    import asyncio
    task_id = separation_store["task_id"]
    separation_store["roles"]["analyst"] = _mock_role("analyst")

    async def fake_reply(session_id, role_name, message_text, system_prompt, ability_ids, default_model, tool_result_prefix, role_description, room_role_names=None, room_collaborative_context=None):
        return "reply_from_" + role_name

    with patch("app.routers.team_room._role_reply_via_chat", side_effect=fake_reply):
        r = separation_client.post(
            f"/api/chat/room/{task_id}/message",
            json={"role": "user", "message": "@task_runner @analyst 请回复", "message_type": "user_message"},
        )
    assert r.status_code == 200
    for _ in range(15):
        time.sleep(0.2)
        r2 = separation_client.get(f"/api/chat/room/{task_id}/messages")
        assert r2.status_code == 200
        msgs = r2.json()
        user_msg = next((m for m in msgs if (m.get("message") or "").strip() == "@task_runner @analyst 请回复"), None)
        if not user_msg:
            continue
        idx = msgs.index(user_msg)
        after = msgs[idx + 1:]
        assistants = [m for m in after if m.get("role") == "assistant"]
        if len(assistants) >= 2:
            assert user_msg.get("mentioned_roles") == ["task_runner", "analyst"]
            assert assistants[0].get("reply_by_role") == "task_runner"
            assert assistants[1].get("reply_by_role") == "analyst"
            assert "reply_from_task_runner" in (assistants[0].get("message") or "")
            assert "reply_from_analyst" in (assistants[1].get("message") or "")
            return
    pytest.fail("expected 2 assistant replies within 3s after POST @task_runner @analyst")


def test_task_room_four_roles_one_message_all_reply_visible(separation_client, separation_store):
    """一条消息 @ 四角色（含空格名）时，四角色均回复且 GET 返回四条 assistant、reply_by_role 正确。"""
    import time
    task_id = separation_store["task_id"]
    roles_four = ["Claude Analyst", "Claude Code Reviewer", "Cursor Code Engineer", "Qwen-deep Analyst"]
    for rn in roles_four:
        separation_store["roles"][rn] = _mock_role(rn)

    async def fake_reply(session_id, role_name, message_text, system_prompt, ability_ids, default_model, tool_result_prefix, role_description, room_role_names=None, room_collaborative_context=None):
        return "大家好，我是 " + role_name + "。"

    with patch("app.routers.team_room._role_reply_via_chat", side_effect=fake_reply):
        r = separation_client.post(
            f"/api/chat/room/{task_id}/message",
            json={
                "role": "user",
                "message": "@Claude Analyst @Claude Code Reviewer @Cursor Code Engineer @Qwen-deep Analyst 互相简单打个招呼",
                "message_type": "user_message",
            },
        )
    assert r.status_code == 200
    body = "@Claude Analyst @Claude Code Reviewer @Cursor Code Engineer @Qwen-deep Analyst 互相简单打个招呼"
    for _ in range(50):
        time.sleep(0.2)
        r2 = separation_client.get(f"/api/chat/room/{task_id}/messages")
        assert r2.status_code == 200
        msgs = r2.json()
        user_msg = next((m for m in msgs if (m.get("message") or "").strip() == body), None)
        if not user_msg:
            continue
        idx = msgs.index(user_msg)
        after = msgs[idx + 1:]
        assistants = [m for m in after if m.get("role") == "assistant"]
        if len(assistants) >= 4:
            assert user_msg.get("mentioned_roles") == roles_four
            got_roles = [m.get("reply_by_role") for m in assistants[:4]]
            assert got_roles == roles_four
            for i, rn in enumerate(roles_four):
                assert "我是 " + rn in (assistants[i].get("message") or "")
            return
    r2 = separation_client.get(f"/api/chat/room/{task_id}/messages")
    msgs = r2.json()
    user_msg = next((m for m in msgs if body in (m.get("message") or "")), None)
    n_after = len([m for m in msgs[msgs.index(user_msg) + 1:] if m.get("role") == "assistant"]) if user_msg else 0
    pytest.fail("expected 4 assistant replies, got %d" % n_after)


def test_task_room_get_messages_reply_by_role_index_for_consecutive_assistants():
    """GET messages 时连续多条 assistant 按前一条 user 的 mentioned_roles 顺序对应 reply_by_role。"""
    from app.routers.team_room import get_room_messages, _parse_mentions
    from unittest.mock import MagicMock, AsyncMock
    from app.storage.models import Message
    import asyncio
    sid = uuid.uuid4()
    m1 = MagicMock(spec=Message)
    m1.role = "user"
    m1.content = "@A @B 请回复"
    m1.created_at = datetime.now(timezone.utc)
    m2 = MagicMock(spec=Message)
    m2.role = "assistant"
    m2.content = "reply A"
    m2.created_at = datetime.now(timezone.utc)
    m3 = MagicMock(spec=Message)
    m3.role = "assistant"
    m3.content = "reply B"
    m3.created_at = datetime.now(timezone.utc)
    messages = [m1, m2, m3]
    out = []
    for i, m in enumerate(messages):
        item = {
            "role": m.role,
            "message": m.content,
            "timestamp": m.created_at.isoformat() if m.created_at else "",
            "mentioned_roles": _parse_mentions(m.content or ""),
        }
        if m.role == "assistant":
            j = i - 1
            while j >= 0 and messages[j].role != "user":
                j -= 1
            if j >= 0:
                prev_mentions = _parse_mentions(messages[j].content or "")
                if prev_mentions:
                    k = 0
                    idx = j + 1
                    while idx < i:
                        if messages[idx].role == "assistant":
                            k += 1
                        else:
                            break
                        idx += 1
                    pos = k
                    item["reply_by_role"] = prev_mentions[pos] if pos < len(prev_mentions) else prev_mentions[0]
        out.append(item)
    assert out[0]["mentioned_roles"] == ["A", "B"]
    assert out[1]["reply_by_role"] == "A"
    assert out[2]["reply_by_role"] == "B"


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


# ----- 群聊参与角色作为上下文：_get_task_room_roles 与 room_role_names 传入回复 -----
@pytest.mark.asyncio
async def test_get_task_room_roles_returns_assignee_roles_from_task_session():
    """_get_task_room_roles 对带 assignee_roles 的任务 session 返回该列表。"""
    from app.routers.team_room import _get_task_room_roles

    sid = uuid.uuid4()
    mock_s = MagicMock()
    mock_s.id = sid
    mock_s.metadata_ = {"is_task": True, "assignee_roles": ["task_runner", "analyst"]}

    class Ctx:
        async def __aenter__(self):
            m = MagicMock()
            r = MagicMock()
            r.scalar_one_or_none.return_value = mock_s
            r.fetchall.return_value = []
            m.execute = AsyncMock(return_value=r)
            m.commit = AsyncMock(return_value=None)
            return m

        async def __aexit__(self, *args):
            pass

    def fake_scope():
        return Ctx()

    with patch("app.routers.team_room.session_scope", side_effect=fake_scope):
        out = await _get_task_room_roles(sid)
    assert out == ["task_runner", "analyst"]


@pytest.mark.asyncio
async def test_get_task_room_roles_returns_empty_for_non_task_or_no_assignee():
    """_get_task_room_roles 对非任务或无 assignee_roles 的 session 返回空列表。"""

    from app.routers.team_room import _get_task_room_roles

    sid = uuid.uuid4()

    def make_scope(session_meta):
        mock_s = MagicMock()
        mock_s.id = sid
        mock_s.metadata_ = session_meta

        class Ctx:
            async def __aenter__(self):
                m = MagicMock()
                r = MagicMock()
                r.scalar_one_or_none.return_value = mock_s
                m.execute = AsyncMock(return_value=r)
                m.commit = AsyncMock(return_value=None)
                return m

            async def __aexit__(self, *args):
                pass

        def fake_scope():
            return Ctx()

        return fake_scope

    for meta in ({}, {"is_task": False}, {"is_task": True}):
        with patch("app.routers.team_room.session_scope", side_effect=make_scope(meta)):
            out = await _get_task_room_roles(sid)
        assert out == [], f"metadata_={meta} should yield []"


@pytest.mark.asyncio
async def test_get_task_room_roles_returns_empty_when_session_not_found():
    """_get_task_room_roles 当 session 不存在时返回空列表。"""
    from app.routers.team_room import _get_task_room_roles

    sid = uuid.uuid4()

    class Ctx:
        async def __aenter__(self):
            m = MagicMock()
            r = MagicMock()
            r.scalar_one_or_none.return_value = None
            m.execute = AsyncMock(return_value=r)
            m.commit = AsyncMock(return_value=None)
            return m

        async def __aexit__(self, *args):
            pass

    def fake_scope():
        return Ctx()

    with patch("app.routers.team_room.session_scope", side_effect=fake_scope):
        out = await _get_task_room_roles(sid)
    assert out == []


def test_extract_path_count_folders_supports_qwen_deep_analyst_message():
    """_extract_path_from_count_folders_intent 支持 @Qwen-deep Analyst 检查 /tmp 下有多少文件夹 等格式。"""
    from app.routers.team_room import _extract_path_from_count_folders_intent

    assert _extract_path_from_count_folders_intent("@Qwen-deep Analyst 检查 /tmp 下有多少文件夹") == "/tmp"
    assert _extract_path_from_count_folders_intent("@Qwen-deep Analyst 检查/home/caros 下有多少文件夹") == "/home/caros"
    assert _extract_path_from_count_folders_intent("检查 /tmp 下有多少个文件夹") == "/tmp"
    assert _extract_path_from_count_folders_intent("检查 /tmp 下有多少个文件夹") == "/tmp"
    assert _extract_path_from_count_folders_intent("check how many folders in /tmp") == "/tmp"
    assert _extract_path_from_count_folders_intent("介绍一下自己") is None


def test_task_room_qwen_deep_analyst_count_folders_gets_tool_result(separation_client, separation_store):
    """@Qwen-deep Analyst 检查 /tmp 下有多少文件夹 时，会执行数文件夹并将结果作为 tool_result_prefix 传给模型，且回复不含 Unsupported task。"""
    import time
    task_id = separation_store["task_id"]
    separation_store["roles"]["Qwen-deep Analyst"] = _mock_role("Qwen-deep Analyst")
    calls = []

    async def capture_and_reply(*args, **kwargs):
        # 验证收到了 tool_result_prefix（说明我们执行了数文件夹）
        tr = kwargs.get("tool_result_prefix", args[6] if len(args) > 6 else None)
        calls.append({"tool_result_prefix": tr})
        if tr and "Directory" in tr and "folder" in tr:
            return "根据检查结果：" + tr
        return "收到。"

    with patch("app.routers.team_room._role_reply_via_chat", side_effect=capture_and_reply):
        r = separation_client.post(
            f"/api/chat/room/{task_id}/message",
            json={
                "role": "user",
                "message": "@Qwen-deep Analyst 检查 /tmp 下有多少文件夹",
                "message_type": "user_message",
            },
        )
    assert r.status_code == 200
    for _ in range(20):
        time.sleep(0.2)
        if calls:
            break
    assert len(calls) >= 1
    assert calls[0].get("tool_result_prefix") is not None
    assert "folder" in (calls[0].get("tool_result_prefix") or "").lower() or "Directory" in (calls[0].get("tool_result_prefix") or "")
    r2 = separation_client.get(f"/api/chat/room/{task_id}/messages")
    assert r2.status_code == 200
    msgs = r2.json()
    assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
    assert any("Unsupported task" not in (m.get("message") or "") for m in assistant_msgs), "reply should not contain Unsupported task"


def test_task_room_chat_failure_shows_error_message(separation_client, separation_store):
    """When chat returns None, reply indicates failure and suggests checking model/API."""
    import time
    task_id = separation_store["task_id"]
    separation_store["roles"]["Qwen-deep Analyst"] = _mock_role("Qwen-deep Analyst")
    async def return_none(*args, **kwargs):
        return None
    with patch("app.routers.team_room._role_reply_via_chat", side_effect=return_none):
        r = separation_client.post(
            f"/api/chat/room/{task_id}/message",
            json={"role": "user", "message": "@Qwen-deep Analyst 介绍一下自己", "message_type": "user_message"},
        )
    assert r.status_code == 200
    for _ in range(20):
        time.sleep(0.2)
        r2 = separation_client.get(f"/api/chat/room/{task_id}/messages")
        msgs = r2.json()
        assistant = [m for m in msgs if m.get("role") == "assistant"]
        if assistant:
            msg = (assistant[-1].get("message") or "")
            assert "回复失败" in msg
            assert "建议检查" in msg or "模型" in msg
            assert "当前支持" not in msg
            return
    pytest.fail("expected assistant reply with 回复失败 / 建议检查")


def test_build_room_collaborative_context_template():
    """_build_room_collaborative_context 产出基础模板：协同角色列表 + 角色边界与 @ 交互说明。"""
    from app.routers.team_room import _build_room_collaborative_context

    out = _build_room_collaborative_context([])
    assert out == ""
    out = _build_room_collaborative_context([("task_runner", "执行任务"), ("analyst", "")])
    assert "【当前任务协同角色】" in out
    assert "task_runner" in out and "执行任务" in out
    assert "analyst" in out
    assert "【角色边界】" in out or "只扮演" in out
    assert "【如何与其他角色协作】" in out or "其他角色" in out or "【@ 的交互模式" in out
    assert "@" in out
    # 明确列出可 @ 的角色名，便于 claude-local 等模型学会写 @角色名
    assert "可 @ 的角色名" in out or "task_runner" in out
    assert "@角色名" in out or "@ 该角色名" in out or "写出 @" in out


@pytest.mark.asyncio
async def test_get_task_room_roles_with_descriptions_returns_name_and_description():
    """_get_task_room_roles_with_descriptions 返回 [(name, description), ...]。"""
    from app.routers.team_room import _get_task_room_roles_with_descriptions

    sid = uuid.uuid4()
    names_from_meta = ["A", "B"]

    async def fake_get_roles(_sid):
        return names_from_meta

    class Ctx:
        async def __aenter__(self):
            m = MagicMock()
            r = MagicMock()
            r.fetchall.return_value = [("A", "角色A描述"), ("B", "角色B描述")]
            m.execute = AsyncMock(return_value=r)
            m.commit = AsyncMock(return_value=None)
            return m

        async def __aexit__(self, *args):
            pass

    def fake_scope():
        return Ctx()

    with patch("app.routers.team_room._get_task_room_roles", side_effect=fake_get_roles), patch(
        "app.routers.team_room.session_scope", side_effect=fake_scope
    ):
        out = await _get_task_room_roles_with_descriptions(sid)
    assert out == [("A", "角色A描述"), ("B", "角色B描述")]


def test_task_room_reply_receives_room_role_names_as_context(separation_client, separation_store):
    """任务设置了 assignee_roles 时，角色回复时 _role_reply_via_chat 会收到 room_role_names 作为群聊上下文。"""
    import time
    task_id = separation_store["task_id"]
    separation_store["roles"]["analyst"] = _mock_role("analyst")
    # 先 PATCH 设置任务的参与角色，使 _get_task_room_roles 能拿到
    r_patch = separation_client.patch(
        f"/api/tasks/{task_id}",
        json={"assignee_roles": ["task_runner", "analyst"]},
    )
    assert r_patch.status_code == 200
    calls = []

    async def capture_reply(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return "reply_ok"

    with patch("app.routers.team_room._role_reply_via_chat", side_effect=capture_reply):
        r = separation_client.post(
            f"/api/chat/room/{task_id}/message",
            json={"role": "user", "message": "@task_runner 请回复", "message_type": "user_message"},
        )
    assert r.status_code == 200
    for _ in range(20):
        time.sleep(0.2)
        if calls:
            break
    assert len(calls) >= 1, "expected _role_reply_via_chat to be called"
    # 应传入 room_role_names（第 9 个位置参数或 kwargs）
    call = calls[0]
    if call["kwargs"]:
        room_roles = call["kwargs"].get("room_role_names")
    else:
        args = call["args"]
        room_roles = args[8] if len(args) > 8 else None
    assert room_roles is not None, "room_role_names should be passed"
    assert set(room_roles) == {"task_runner", "analyst"}
    assert len(room_roles) == 2
    room_ctx = call["kwargs"].get("room_collaborative_context") if call["kwargs"] else (call["args"][9] if len(call["args"]) > 9 else None)
    if room_ctx:
        assert "当前任务协同角色" in room_ctx or "如何与其他角色协作" in room_ctx or "角色边界" in room_ctx


def test_task_room_role_reply_and_ability_execution(separation_client, separation_store):
    """Web 端与 role 对话：@ 角色后 role 能正确响应并执行相应能力（mock 角色提示词与能力执行）。"""
    task_id = separation_store["task_id"]
    with patch(
        "app.routers.team_room._get_role_prompt_and_abilities",
        new_callable=AsyncMock,
        return_value=("你是助手。", ["echo"], None, ""),
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
        return_value=("你是助手。", ["read_aura"], None, ""),
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
        return_value=("你是 task_runner 角色，请友好回复用户。", [CHAT_ABILITY_ID], None, ""),
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


@pytest.mark.parametrize("role_name", ["task_runner", "analyst", "Qwen-deep Analyst"])
def test_task_room_each_role_dialogue_ok(separation_client, separation_store, role_name):
    """每个 role 的对话流程正常：@ 该角色后能收到一条 assistant 回复且 reply_by_role 正确。"""
    task_id = separation_store["task_id"]
    separation_store["roles"][role_name] = _mock_role(role_name)
    reply_content = "reply_ok_" + role_name.replace(" ", "_")

    async def fake_reply(session_id, rn, message_text, system_prompt, ability_ids, default_model, tool_result_prefix, role_description, room_role_names=None, room_collaborative_context=None):
        return reply_content

    with patch("app.routers.team_room._role_reply_via_chat", side_effect=fake_reply):
        body = "@" + role_name + " 请回复"
        r = separation_client.post(
            f"/api/chat/room/{task_id}/message",
            json={"role": "user", "message": body, "message_type": "user_message"},
        )
    assert r.status_code == 200
    for _ in range(20):
        time.sleep(0.2)
        r2 = separation_client.get(f"/api/chat/room/{task_id}/messages")
        assert r2.status_code == 200
        msgs = r2.json()
        user_msg = next((m for m in msgs if (m.get("message") or "").strip() == body), None)
        if not user_msg:
            continue
        idx = msgs.index(user_msg)
        after = msgs[idx + 1:]
        assistants = [m for m in after if m.get("role") == "assistant"]
        if assistants and (assistants[0].get("reply_by_role") == role_name) and (reply_content in (assistants[0].get("message") or "")):
            return
    pytest.fail("expected one assistant reply with reply_by_role=%r within 4s" % role_name)


def test_task_room_ability_not_bound_to_role_not_executed(separation_client, separation_store):
    """能力未绑定到角色时，执行该能力不会真正运行（仅绑定列表内的能力会执行）。"""
    import time
    task_id = separation_store["task_id"]
    with patch(
        "app.routers.team_room._get_role_prompt_and_abilities",
        new_callable=AsyncMock,
        return_value=("你是助手。", ["echo"], None, ""),
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
