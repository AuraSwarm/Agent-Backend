"""
设计逻辑测试：按 design.md 确保功能按设计运行。

- 角色：名称唯一、状态 enabled/disabled、能力绑定、提示词版本（创建 v1，更新追加版本）、仅返回最新提示词。
- 任务/群聊：任务列表仅 status==1、limit 上限 100、session 不存在时 POST message 返回 404、无效 session_id 返回 400。
- 能力：来自 config local_tools，列表供绑定。
"""

import uuid

from fastapi.testclient import TestClient


# --- 角色逻辑（设计：名称唯一、状态、能力、提示词版本）---

def test_design_role_name_unique_returns_400(client_stateful):
    """设计：角色名唯一，重复创建返回 400，文案含 already exists。"""
    payload = {"name": "unique_role", "description": "d", "status": "enabled", "abilities": [], "system_prompt": "p"}
    r1 = client_stateful.post("/api/admin/roles", json=payload)
    assert r1.status_code == 200
    r2 = client_stateful.post("/api/admin/roles", json=payload)
    assert r2.status_code == 400
    assert "already exists" in (r2.json().get("detail") or "").lower()


def test_design_role_status_enabled_disabled(client_full_stateful):
    """设计：角色状态为 enabled 或 disabled，创建/更新后可正确返回。"""
    name = "status_role"
    client_full_stateful.post(
        "/api/admin/roles",
        json={"name": name, "description": "x", "status": "disabled", "abilities": [], "system_prompt": "x"},
    )
    r = client_full_stateful.get(f"/api/admin/roles/{name}")
    assert r.status_code == 200
    assert r.json().get("status") == "disabled"
    client_full_stateful.put(f"/api/admin/roles/{name}", json={"status": "enabled"})
    r2 = client_full_stateful.get(f"/api/admin/roles/{name}")
    assert r2.status_code == 200
    assert r2.json().get("status") == "enabled"


def test_design_role_get_returns_latest_prompt_only(client_full_stateful):
    """设计：GET 角色仅返回最新版本提示词（版本历史在服务端，接口只暴露最新）。"""
    name = "prompt_role"
    client_full_stateful.post(
        "/api/admin/roles",
        json={"name": name, "description": "", "status": "enabled", "abilities": [], "system_prompt": "v1 content"},
    )
    r1 = client_full_stateful.get(f"/api/admin/roles/{name}")
    assert r1.status_code == 200
    assert (r1.json().get("system_prompt") or "") == "v1 content"
    client_full_stateful.put(f"/api/admin/roles/{name}", json={"system_prompt": "v2 content"})
    r2 = client_full_stateful.get(f"/api/admin/roles/{name}")
    assert r2.status_code == 200
    assert (r2.json().get("system_prompt") or "") == "v2 content"


def test_design_role_update_partial_fields(client_full_stateful):
    """设计：PUT 支持部分字段更新，未传字段保持不变。"""
    name = "partial_role"
    client_full_stateful.post(
        "/api/admin/roles",
        json={
            "name": name,
            "description": "orig_desc",
            "status": "enabled",
            "abilities": ["echo"],
            "system_prompt": "orig_prompt",
        },
    )
    client_full_stateful.put(f"/api/admin/roles/{name}", json={"description": "new_desc"})
    r = client_full_stateful.get(f"/api/admin/roles/{name}")
    assert r.status_code == 200
    assert r.json().get("description") == "new_desc"
    assert r.json().get("status") == "enabled"
    assert r.json().get("abilities") == ["echo"]
    assert (r.json().get("system_prompt") or "") == "orig_prompt"


def test_design_role_update_abilities_replaces_not_append(client_full_stateful):
    """设计：更新能力时先清空再写入，即完全替换。"""
    name = "ability_role"
    client_full_stateful.post(
        "/api/admin/roles",
        json={"name": name, "description": "", "status": "enabled", "abilities": ["echo"], "system_prompt": ""},
    )
    client_full_stateful.put(f"/api/admin/roles/{name}", json={"abilities": ["date"]})
    r = client_full_stateful.get(f"/api/admin/roles/{name}")
    assert r.status_code == 200
    assert r.json().get("abilities") == ["date"]
    client_full_stateful.put(f"/api/admin/roles/{name}", json={"abilities": ["echo", "date"]})
    r2 = client_full_stateful.get(f"/api/admin/roles/{name}")
    assert set(r2.json().get("abilities") or []) == {"echo", "date"}


def test_design_role_create_with_empty_prompt_and_abilities(client_full_stateful):
    """设计：允许空提示词、空能力列表创建角色。"""
    name = "empty_role"
    r = client_full_stateful.post(
        "/api/admin/roles",
        json={"name": name, "description": "", "status": "enabled", "abilities": [], "system_prompt": ""},
    )
    assert r.status_code == 200
    get_r = client_full_stateful.get(f"/api/admin/roles/{name}")
    assert get_r.status_code == 200
    assert (get_r.json().get("system_prompt") or "") == ""
    assert get_r.json().get("abilities") == []


def test_design_role_put_nonexistent_404(client):
    """设计：PUT 不存在的角色返回 404。"""
    r = client.put(
        "/api/admin/roles/nonexistent_role_404",
        json={"description": "x", "status": "enabled", "abilities": [], "system_prompt": "x"},
    )
    assert r.status_code == 404
    assert "detail" in r.json()


# --- 任务/群聊逻辑（设计：按 session 隔离、仅存在会话可发消息）---

def test_design_room_invalid_session_id_400(client):
    """设计：room 接口 session_id 非法 UUID 时返回 400。"""
    r_get = client.get("/api/chat/room/not-a-uuid/messages")
    assert r_get.status_code == 400
    assert "invalid" in (r_get.json().get("detail") or "").lower()
    r_post = client.post(
        "/api/chat/room/not-a-uuid/message",
        json={"role": "user", "message": "hi", "message_type": "user_message"},
    )
    assert r_post.status_code == 400


def test_design_room_post_message_requires_existing_session(client):
    """设计：POST message 仅当 session 存在时成功，否则 404。"""
    sid = str(uuid.uuid4())
    r = client.post(
        f"/api/chat/room/{sid}/message",
        json={"role": "user", "message": "test", "message_type": "user_message"},
    )
    assert r.status_code == 404
    assert "detail" in r.json()


def test_design_tasks_list_limit_capped_at_100(client):
    """设计：GET /api/tasks limit 参数上限 100，超过时按 100 处理。"""
    r = client.get("/api/tasks", params={"limit": 200})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) <= 100


def test_design_tasks_response_shape(client):
    """设计：任务列表项包含 id、title、status、last_updated。"""
    r = client.get("/api/tasks")
    assert r.status_code == 200
    for item in r.json():
        assert "id" in item
        assert "title" in item
        assert "status" in item
        assert "last_updated" in item
        assert item["status"] in ("in_progress", "completed")


def test_design_room_messages_ordered_by_time(client_task_center, task_center_state):
    """设计：群聊消息按时间升序返回（created_at.asc）。"""
    sessions_list, messages_by_session = task_center_state
    client_task_center.post("/sessions", json={"title": "order_test"})
    session_id = str(sessions_list[0].id)
    for i, msg in enumerate(["first", "second", "third"]):
        client_task_center.post(
            f"/api/chat/room/{session_id}/message",
            json={"role": "user", "message": msg, "message_type": "user_message"},
        )
    r = client_task_center.get(f"/api/chat/room/{session_id}/messages")
    assert r.status_code == 200
    msgs = r.json()
    assert len(msgs) == 3
    assert [m["message"] for m in msgs] == ["first", "second", "third"]


def test_design_room_message_body_role_and_type(client_task_center, task_center_state):
    """设计：消息体支持 role、message、message_type（user_message/ai_message/system_message）。"""
    sessions_list, messages_by_session = task_center_state
    client_task_center.post("/sessions", json={"title": "role_test"})
    session_id = str(sessions_list[0].id)
    r = client_task_center.post(
        f"/api/chat/room/{session_id}/message",
        json={"role": "user", "message": "hello", "message_type": "user_message"},
    )
    assert r.status_code == 200
    msgs = client_task_center.get(f"/api/chat/room/{session_id}/messages").json()
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["message"] == "hello"


# --- 能力逻辑（设计：能力来自 local_tools，供角色绑定）---

def test_design_abilities_list_from_config(client):
    """设计：GET /api/abilities 返回配置中的 local_tools，每项含 id、name、description。"""
    r = client.get("/api/abilities")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    for a in data:
        assert "id" in a
        assert "name" in a
        assert "description" in a


def test_design_role_can_bind_ability_ids_from_list(client_full_stateful):
    """设计：角色可绑定的 ability_id 与 GET /api/abilities 的 id 一致即可绑定。"""
    ab_r = client_full_stateful.get("/api/abilities")
    assert ab_r.status_code == 200
    ids = [a["id"] for a in ab_r.json()]
    name = "bind_ability_role"
    ability_subset = ids[:1] if ids else []
    r = client_full_stateful.post(
        "/api/admin/roles",
        json={"name": name, "description": "", "status": "enabled", "abilities": ability_subset, "system_prompt": ""},
    )
    assert r.status_code == 200
    get_r = client_full_stateful.get(f"/api/admin/roles/{name}")
    assert get_r.status_code == 200
    assert get_r.json().get("abilities") == ability_subset


# --- 列表与详情一致性 ---

def test_design_list_roles_and_get_role_consistent(client_full_stateful):
    """设计：list 中的角色与 GET /admin/roles/{name} 详情一致（name、status、abilities）。"""
    name = "consistent_role"
    client_full_stateful.post(
        "/api/admin/roles",
        json={"name": name, "description": "d", "status": "enabled", "abilities": ["echo"], "system_prompt": "p"},
    )
    list_r = client_full_stateful.get("/api/admin/roles")
    assert list_r.status_code == 200
    found = next((x for x in list_r.json() if x["name"] == name), None)
    assert found is not None
    get_r = client_full_stateful.get(f"/api/admin/roles/{name}")
    assert get_r.status_code == 200
    detail = get_r.json()
    assert found["name"] == detail["name"]
    assert found["status"] == detail["status"]
    assert found["abilities"] == detail["abilities"]
