"""
Aura 启动后 Web 端各页面的外观与表现测试。

在 WEB_UI_DIR 指向 Web-Service/static 时（即 Aura 启动后的状态），
验证 GET /（主对话）、GET /team/（任务中心）、GET /team/admin/roles.html（员工角色管理）
返回 200，且 HTML 包含预期结构：标题、侧栏导航、主内容区、关键交互元素。

使用 test_ui_pages 的 client fixture（conftest 会在存在 Web-Service 时设置 WEB_UI_DIR）。
"""

import pytest
from fastapi.testclient import TestClient


# --- 主对话页 GET / ---
def test_aura_main_page_loads(client: TestClient):
    """主对话页：GET / 返回 200，包含标题、侧栏、消息区、输入与发送。"""
    r = client.get("/")
    if r.status_code == 404:
        pytest.skip("WEB_UI_DIR not set (main UI not mounted)")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    html = r.text
    assert "主对话" in html or "Chat" in html
    assert "sidebar" in html or "layout-root" in html
    assert "messages" in html or 'id="messages"' in html
    assert "任务中心" in html or "team" in html
    assert "input" in html or "textarea" in html
    assert "sendBtn" in html or "send" in html.lower()


def test_aura_main_page_static_assets(client: TestClient):
    """主对话页依赖的 /static 资源可访问。"""
    r = client.get("/")
    if r.status_code == 404:
        pytest.skip("WEB_UI_DIR not set")
    r_css = client.get("/static/style.css")
    r_js = client.get("/static/app.js")
    assert r_css.status_code == 200, "GET /static/style.css"
    assert r_js.status_code == 200, "GET /static/app.js"


# --- 任务中心 GET /team/ ---
def test_aura_task_center_page_appearance(client: TestClient):
    """任务中心页：GET /team/ 返回 200，包含标题、侧栏、任务列表区、聊天区、输入与操作按钮。"""
    r = client.get("/team/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    html = r.text
    assert "任务中心" in html or "AI" in html
    assert "task-list" in html
    assert "chat-container" in html
    assert "task-title" in html
    assert "user-input" in html
    assert "send-btn" in html
    assert "返回主对话" in html
    assert "员工角色" in html or "roles" in html
    assert "清空消息" in html or "删除任务" in html or "task-actions" in html


def test_aura_task_center_static_assets(client: TestClient):
    """任务中心静态资源：/team/style.css、/team/script.js 可访问。"""
    for path in ["/team/style.css", "/team/script.js"]:
        r = client.get(path)
        assert r.status_code == 200, f"GET {path}"


# --- 员工角色管理 GET /team/admin/roles.html ---
def test_aura_roles_page_appearance(client: TestClient):
    """员工角色管理页：GET /team/admin/roles.html 返回 200，包含标题、侧栏、角色列表、新建按钮、弹窗表单。"""
    r = client.get("/team/admin/roles.html")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    html = r.text
    assert "角色管理" in html or "员工" in html
    assert "roles-list" in html
    assert "create-role-btn" in html or "新建角色" in html
    assert "role-modal" in html
    assert "role-form" in html
    assert "role-name" in html
    assert "role-prompt" in html
    assert "返回主对话" in html
    assert "任务中心" in html


def test_aura_roles_page_static_asset(client: TestClient):
    """员工角色管理脚本 /team/admin/admin-roles.js 可访问。"""
    r = client.get("/team/admin/admin-roles.js")
    assert r.status_code == 200
    assert "fetch" in r.text or "api" in r.text.lower()


# --- 三页共同：导航一致性与深色主题 ---
def test_aura_all_pages_nav_consistency(client: TestClient):
    """三页侧栏导航一致：主对话/任务中心/员工角色管理 或 返回主对话 链接存在。"""
    pages = [
        ("/", "主对话" if client.get("/").status_code == 200 else None),
        ("/team/", "任务中心"),
        ("/team/admin/roles.html", "员工角色"),
    ]
    for path, label in pages:
        if label is None:
            continue
        r = client.get(path)
        assert r.status_code == 200, path
        html = r.text
        assert "任务中心" in html or "team" in html.lower(), f"{path} should link to task center"
        assert "员工" in html or "角色" in html or "roles" in html.lower(), f"{path} should link to roles"


def test_aura_pages_use_unified_style(client: TestClient):
    """任务中心与角色管理页引用统一样式（深色主题变量或 style.css）。"""
    for path in ["/team/", "/team/admin/roles.html"]:
        r = client.get(path)
        assert r.status_code == 200
        assert "/team/style.css" in r.text, f"{path} should load /team/style.css"
