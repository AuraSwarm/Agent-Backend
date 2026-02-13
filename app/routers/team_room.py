"""
AI 员工团队：任务列表与群聊通道 API。

- GET /api/tasks：任务列表（基于 sessions）
- POST /api/tasks：独立创建任务（不需与 chat 绑定）
- GET /api/chat/room/{session_id}/messages：群聊消息列表
- POST /api/chat/room/{session_id}/message：用户反馈（写入即触发）
"""

import asyncio
import re
import uuid
from pathlib import Path

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, insert

from app.config.loader import get_config
from app.constants import CHAT_ABILITY_ID
from app.storage.db import session_scope
from app.storage.models import CustomAbility, Message, Session
from app.tools.runner import execute_local_tool
from memory_base.models_team import EmployeeRole, PromptVersion, RoleAbility

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api", tags=["team_room"])


class RoomMessageBody(BaseModel):
    role: str = "user"
    message: str
    message_type: str = "user_message"


class CreateTaskBody(BaseModel):
    title: str | None = None
    assignee_role: str | None = None  # legacy: single role
    assignee_roles: list[str] | None = None  # multiple roles


def _task_meta_assignee_roles(meta: dict) -> list[str]:
    """Normalize assignee_roles from metadata (supports legacy assignee_role)."""
    if "assignee_roles" in meta and isinstance(meta["assignee_roles"], list):
        return [str(r).strip() for r in meta["assignee_roles"] if r and str(r).strip()]
    if meta.get("assignee_role"):
        return [str(meta["assignee_role"]).strip()]
    return []


def _session_to_task_item(s) -> dict:
    meta = getattr(s, "metadata_", None) or {}
    return {
        "id": str(s.id),
        "title": s.title or "未命名任务",
        "status": "completed" if s.status != 1 else "in_progress",
        "last_updated": s.updated_at.isoformat() if s.updated_at else "",
        "assignee_roles": _task_meta_assignee_roles(meta),
    }


def _is_task_session(s) -> bool:
    return bool((getattr(s, "metadata_", None) or {}).get("is_task"))


def _extract_path_from_count_folders_intent(text: str) -> str | None:
    if "多少文件夹" not in text or "下" not in text:
        return None
    m = re.search(r"检查\s*([^\s]+)\s*下\s*有多少文件夹", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"检查\s*(/[^\s]*)\s*.*多少文件夹", text)
    if m:
        return m.group(1).strip()
    return None


def _process_task_message(message_text: str) -> str:
    path = _extract_path_from_count_folders_intent(message_text)
    if path is None:
        return "Unsupported task. Example: 检查 /home/caros 下有多少文件夹"
    p = Path(path)
    if not p.exists():
        return "Path not found: " + path
    if not p.is_dir():
        return "Not a directory: " + path
    count = sum(1 for _ in p.iterdir() if _.is_dir())
    return "Directory " + path + " has " + str(count) + " folder(s)."


async def _get_role_prompt_and_abilities(role_name: str) -> tuple[str, list[str], str | None]:
    """Load role's latest system prompt, ability ids, and default_model. Returns ('', [], None) if role not found."""
    async with session_scope() as db:
        r = await db.execute(select(EmployeeRole).where(EmployeeRole.name == role_name))
        role = r.scalar_one_or_none()
        if not role:
            return ("", [], None)
        r_ab = await db.execute(select(RoleAbility.ability_id).where(RoleAbility.role_name == role_name))
        abilities = [row[0] for row in r_ab.fetchall()]
        if CHAT_ABILITY_ID not in abilities:
            abilities = [CHAT_ABILITY_ID] + abilities
        r_pv = await db.execute(
            select(PromptVersion)
            .where(PromptVersion.role_name == role_name)
            .order_by(PromptVersion.version.desc())
            .limit(1)
        )
        latest = r_pv.scalar_one_or_none()
        system_prompt = (latest.content or "").strip() if latest else ""
        default_model = getattr(role, "default_model", None)
        return (system_prompt, abilities, default_model)


async def _load_custom_abilities_for_tools() -> list[dict]:
    """Load custom abilities from DB for tool execution (same shape as tools router)."""
    async with session_scope() as db:
        r = await db.execute(select(CustomAbility))
        rows = r.scalars().all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "description": row.description or "",
            "command": row.command,
            "prompt_template": getattr(row, "prompt_template", None) or None,
        }
        for row in rows
    ]


def _builtin_chat_ability_dict() -> dict:
    """内置对话能力（每个角色必备），无 command/prompt_template，仅表示可对话。"""
    return {
        "id": CHAT_ABILITY_ID,
        "name": "对话",
        "description": "与用户进行文字对话",
        "command": [],
        "prompt_template": None,
    }


async def _get_ability_by_id(ability_id: str) -> dict | None:
    """Resolve ability by id from builtin + config + custom (custom overrides). Returns dict with id, name, description, command, prompt_template (optional)."""
    config = get_config()
    custom = await _load_custom_abilities_for_tools()
    by_id: dict[str, dict] = {CHAT_ABILITY_ID: _builtin_chat_ability_dict()}
    for t in getattr(config, "local_tools", None) or []:
        by_id[t.id] = {
            "id": t.id,
            "name": getattr(t, "name", t.id),
            "description": getattr(t, "description", ""),
            "command": t.command if isinstance(t.command, list) else [t.command],
            "prompt_template": None,
        }
    for c in custom:
        by_id[c["id"]] = c
    return by_id.get(ability_id)


async def _run_prompt_ability(prompt_template: str, user_message: str) -> str:
    """Execute prompt-type ability: format template with {message}, call LLM, return content."""
    from app.adapters.cloud import CloudAPIAdapter

    prompt = prompt_template.replace("{message}", user_message).replace("{user_message}", user_message)
    config = get_config()
    chat_providers = getattr(config, "chat_providers", {}) or {}
    default_chat = getattr(config, "default_chat_provider", None) or "dashscope"
    if default_chat not in chat_providers:
        return "[提示词能力需要配置 chat_providers]"
    prov = chat_providers[default_chat]
    _get = lambda p, k, d=None: getattr(p, k, d) if not isinstance(p, dict) else p.get(k, d)
    model = _get(prov, "model") or ("qwen-max" if not (_get(prov, "models")) else (_get(prov, "models") or [])[0])
    adapter = CloudAPIAdapter(
        api_key_env=_get(prov, "api_key_env", "DASHSCOPE_API_KEY"),
        endpoint=(_get(prov, "endpoint") or "").rstrip("/"),
        model=model or "qwen-max",
    )
    try:
        text, _ = await adapter.call(prompt, messages=[{"role": "user", "content": prompt}])
        return (text or "").strip() or "(无输出)"
    except Exception as e:
        logger.warning("prompt_ability_run_error", error=str(e))
        return f"[提示词能力执行异常] {e}"


def _parse_execute_intent(text: str) -> tuple[str | None, str]:
    """If text matches '执行 X' or '运行 X' or '用 X 发 ...', return (ability_id, remainder). Else (None, '')."""
    text = (text or "").strip()
    m = re.match(r"(?:执行|运行)\s+([a-zA-Z0-9_-]+)\s*(.*)$", text, re.DOTALL)
    if m:
        return (m.group(1).strip(), (m.group(2) or "").strip())
    m = re.match(r"用\s+([a-zA-Z0-9_-]+)\s+发\s+(.*)$", text, re.DOTALL)
    if m:
        return (m.group(1).strip(), (m.group(2) or "").strip())
    return (None, "")


async def _try_run_ability(
    ability_id: str, allowed_ability_ids: list[str], user_message: str
) -> str | None:
    """If ability_id is in allowed list, execute tool or prompt ability and return result; else None. On error return error string."""
    if ability_id not in allowed_ability_ids:
        return None
    ability = await _get_ability_by_id(ability_id)
    if not ability:
        return f"[能力不存在] {ability_id}"
    prompt_template = (ability.get("prompt_template") or "").strip() or None
    if prompt_template:
        return await _run_prompt_ability(prompt_template, user_message)
    # 对话能力无 command，不执行工具，由后续对话流程处理
    cmd = ability.get("command") or []
    if not cmd or ability_id == CHAT_ABILITY_ID:
        return None
    config = get_config()
    custom = await _load_custom_abilities_for_tools()
    params = {}
    if user_message:
        params["message"] = user_message
    try:
        result = await asyncio.to_thread(
            execute_local_tool, config, ability_id, params, 30, custom
        )
        out = (result.get("stdout") or "").strip()
        err = (result.get("stderr") or "").strip()
        code = result.get("returncode", -1)
        if code != 0 and err:
            return f"[执行 {ability_id} 返回 {code}]\nstderr: {err}\nstdout: {out}"
        return out or "(无输出)"
    except ValueError as e:
        return f"[执行 {ability_id} 失败] {e}"
    except Exception as e:
        logger.warning("task_ability_run_error", ability_id=ability_id, error=str(e))
        return f"[执行 {ability_id} 异常] {e}"


async def _role_reply_via_chat(
    session_id: uuid.UUID,
    role_name: str,
    message_text: str,
    system_prompt: str,
    ability_ids: list[str],
    default_model: str | None,
    tool_result_prefix: str | None,
) -> str | None:
    """Call chat API with role context; return assistant text or None on failure."""
    from app.adapters.cloud import CloudAPIAdapter

    config = get_config()
    chat_providers = getattr(config, "chat_providers", {}) or {}
    default_chat = getattr(config, "default_chat_provider", None) or "dashscope"
    if default_chat not in chat_providers:
        return None
    prov = chat_providers[default_chat]
    _get = lambda p, k, d=None: getattr(p, k, d) if not isinstance(p, dict) else p.get(k, d)
    model = default_model or _get(prov, "model")
    if not model:
        models_list = _get(prov, "models") or []
        model = models_list[0] if models_list else "qwen-max"
    adapter = CloudAPIAdapter(
        api_key_env=_get(prov, "api_key_env", "DASHSCOPE_API_KEY"),
        endpoint=(_get(prov, "endpoint") or "").rstrip("/"),
        model=model or "qwen-max",
    )
    ability_desc = ""
    if ability_ids:
        ability_desc = "\n你可使用以下能力（当用户要求执行时再调用）：" + ", ".join(ability_ids)
    system = (system_prompt or "你是助手。") + ability_desc
    messages = [{"role": "system", "content": system}]
    async with session_scope() as db:
        r = await db.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at.asc())
        )
        for m in r.scalars().all():
            messages.append({"role": m.role, "content": m.content or ""})
    if tool_result_prefix:
        messages.append({"role": "user", "content": f"[上一条已执行能力的结果]\n{tool_result_prefix}"})
    try:
        text, _ = await adapter.call(message_text, messages=messages)
        return text
    except Exception as e:
        logger.warning("task_role_chat_error", role=role_name, error=str(e))
        return None


async def _process_task_and_reply(
    session_id: uuid.UUID, message_text: str, valid_mentions: list[str] | None = None
) -> None:
    valid_mentions = valid_mentions or []
    reply: str
    if valid_mentions:
        role_name = valid_mentions[0]
        system_prompt, ability_ids, default_model = await _get_role_prompt_and_abilities(role_name)
        tool_result_prefix: str | None = None
        ability_id, remainder = _parse_execute_intent(message_text)
        if ability_id and ability_ids:
            tool_result_prefix = await _try_run_ability(ability_id, ability_ids, remainder)
        # 只要有 @ 到有效角色就优先用对话接口回复，无 system_prompt 时用默认提示词，避免返回 "Unsupported task"
        effective_prompt = system_prompt or f"你是角色「{role_name}」。请根据用户消息友好回复；若涉及你具备的能力可说明或执行。"
        chat_reply = await _role_reply_via_chat(
            session_id, role_name, message_text, effective_prompt, ability_ids, default_model, tool_result_prefix
        )
        if chat_reply is not None:
            reply = chat_reply
        else:
            reply = _process_task_message(message_text)
    else:
        reply = _process_task_message(message_text)
    async with session_scope() as db:
        db.add(
            Message(
                id=uuid.uuid4(),
                session_id=session_id,
                role="assistant",
                content=reply,
            )
        )
        await db.commit()


# @-mention: only messages that @ a valid role are direct input; others are context only.
# 支持角色名含空格（如 Claude Analyst）：@ 后匹配单词序列，单词间仅单空格 \s，避免把 "B   hi" 整段匹配
MENTION_PATTERN = re.compile(r"@([a-zA-Z0-9_]+(?:\s[a-zA-Z0-9_]+)*)")


def _parse_mentions(text: str) -> list[str]:
    """Extract @role_name mentions from message content (unique, order preserved)."""
    seen: set[str] = set()
    out: list[str] = []
    for m in MENTION_PATTERN.finditer(text):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


async def _valid_role_names(names: list[str]) -> list[str]:
    """Return subset of names that exist as EmployeeRole (for direct-input semantics)."""
    if not names:
        return []
    async with session_scope() as db:
        r = await db.execute(select(EmployeeRole.name).where(EmployeeRole.name.in_(names)))
        return [row[0] for row in r.fetchall()]


def _resolve_task_metadata(assignee_roles: list[str] | None) -> dict:
    """Build task session metadata (is_task + optional assignee_roles)."""
    meta: dict = {"is_task": True}
    if assignee_roles is not None:
        roles = [str(r).strip() for r in assignee_roles if r and str(r).strip()]
        if roles:
            meta["assignee_roles"] = roles
    return meta


def _normalize_assignee_roles_from_body(body: CreateTaskBody | None) -> list[str] | None:
    """Resolve assignee_roles from body (assignee_roles or legacy assignee_role)."""
    if not body:
        return None
    if body.assignee_roles is not None:
        return [str(r).strip() for r in body.assignee_roles if r and str(r).strip()] or []
    if body.assignee_role is not None and (body.assignee_role or "").strip():
        return [(body.assignee_role or "").strip()]
    return None


@router.post("/tasks")
async def create_task(body: CreateTaskBody | None = None) -> dict:
    """独立创建任务（不依赖 chat）；可选 assignee_roles 分配多个角色。返回与 GET /api/tasks 单项相同结构。"""
    title = (body and body.title and body.title.strip()) or None
    roles = _normalize_assignee_roles_from_body(body)
    if roles:
        async with session_scope() as db:
            r = await db.execute(select(EmployeeRole.name).where(EmployeeRole.name.in_(roles)))
            found = {row[0] for row in r.fetchall()}
            missing = [n for n in roles if n not in found]
            if missing:
                raise HTTPException(status_code=400, detail="assignee_roles not found: " + ", ".join(missing))
    async with session_scope() as db:
        meta = _resolve_task_metadata(roles)
        s = Session(title=title or None, metadata_=meta)
        db.add(s)
        await db.flush()
        item = _session_to_task_item(s)
        await db.commit()
        return item


@router.get("/tasks")
async def get_tasks(limit: int = 50) -> list[dict]:
    """任务列表：仅含 metadata.is_task=true 的 session（DB 层过滤，与对话完全分离）。"""
    limit = min(limit, 100)
    async with session_scope() as db:
        r = await db.execute(
            select(Session)
            .where(Session.status == 1)
            .where(Session.metadata_.contains({"is_task": True}))
            .order_by(Session.updated_at.desc())
            .limit(limit)
        )
        raw = list(r.scalars().all())
    tasks = [s for s in raw if _is_task_session(s)][:limit]
    return [_session_to_task_item(s) for s in tasks]


class UpdateTaskBody(BaseModel):
    title: str | None = None
    assignee_role: str | None = None  # legacy
    assignee_roles: list[str] | None = None  # multiple roles; [] to clear


def _normalize_patch_assignee_roles(body: UpdateTaskBody) -> list[str] | None:
    """None = no change; [] = clear; [names] = set."""
    if body.assignee_roles is not None:
        return [str(r).strip() for r in body.assignee_roles if r and str(r).strip()]
    if body.assignee_role is not None:
        v = (body.assignee_role or "").strip()
        return [v] if v else []
    return None


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: UpdateTaskBody) -> dict:
    """Update task title and/or assignee_roles (分配多个角色)."""
    if body.title is None and body.assignee_role is None and body.assignee_roles is None:
        return {"status": "ok"}
    try:
        sid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="task not found")
    patch_roles = _normalize_patch_assignee_roles(body)
    if patch_roles is not None and len(patch_roles) > 0:
        async with session_scope() as db:
            r = await db.execute(select(EmployeeRole.name).where(EmployeeRole.name.in_(patch_roles)))
            found = {row[0] for row in r.fetchall()}
            missing = [n for n in patch_roles if n not in found]
            if missing:
                raise HTTPException(status_code=400, detail="assignee_roles not found: " + ", ".join(missing))
    async with session_scope() as db:
        r = await db.execute(select(Session).where(Session.id == sid))
        s = r.scalar_one_or_none()
        if not s or not _is_task_session(s):
            raise HTTPException(status_code=404, detail="task not found")
        if body.title is not None:
            s.title = body.title.strip() or None
        if patch_roles is not None:
            meta = dict(getattr(s, "metadata_", None) or {})
            meta["is_task"] = True
            if patch_roles:
                meta["assignee_roles"] = patch_roles
            else:
                meta.pop("assignee_roles", None)
                meta.pop("assignee_role", None)
            s.metadata_ = meta
        await db.commit()
    return {"status": "ok"}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str) -> dict:
    """Delete a task and its messages. 任务与对话分离，删除任务请走此接口。"""
    try:
        sid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="task not found")
    async with session_scope() as db:
        r = await db.execute(select(Session).where(Session.id == sid))
        s = r.scalar_one_or_none()
        if not s or not _is_task_session(s):
            raise HTTPException(status_code=404, detail="task not found")
        await db.execute(delete(Message).where(Message.session_id == sid))
        await db.execute(delete(Session).where(Session.id == sid))
        await db.commit()
    return {"status": "ok", "message": "task deleted"}


class ConvertToTaskBody(BaseModel):
    session_id: str


@router.post("/tasks/from-session")
async def convert_session_to_task(body: ConvertToTaskBody) -> dict:
    """将一条 chat 会话转为任务（写入 metadata.is_task=true），返回任务项。"""
    try:
        sid = uuid.UUID(body.session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id")
    async with session_scope() as db:
        r = await db.execute(select(Session).where(Session.id == sid))
        s = r.scalar_one_or_none()
        if not s:
            raise HTTPException(status_code=404, detail="Session not found")
        meta = dict(s.metadata_ or {})
        meta["is_task"] = True
        s.metadata_ = meta
        item = _session_to_task_item(s)
        await db.commit()
        return item


@router.get("/chat/room/{session_id}/messages")
async def get_room_messages(session_id: str) -> list[dict]:
    """群聊消息列表（仅限任务）。对话请用 GET /sessions/{id}/messages。"""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id")
    async with session_scope() as db:
        r = await db.execute(select(Session).where(Session.id == sid))
        s = r.scalar_one_or_none()
        if not s:
            raise HTTPException(status_code=404, detail="Session not found")
        if not _is_task_session(s):
            raise HTTPException(status_code=404, detail="use GET /sessions/{id}/messages for conversations")
        r2 = await db.execute(
            select(Message).where(Message.session_id == sid).order_by(Message.created_at.asc())
        )
        messages = list(r2.scalars().all())
    out = []
    for i, m in enumerate(messages):
        item = {
            "role": m.role,
            "message": m.content,
            "timestamp": m.created_at.isoformat() if m.created_at else "",
            "mentioned_roles": _parse_mentions(m.content or ""),
        }
        if m.role == "assistant" and i > 0:
            prev = messages[i - 1]
            if prev.role == "user":
                prev_mentions = _parse_mentions(prev.content or "")
                if prev_mentions:
                    item["reply_by_role"] = prev_mentions[0]
        out.append(item)
    return out


@router.post("/chat/room/{session_id}/message")
async def post_room_message(session_id: str, body: RoomMessageBody) -> dict:
    """用户反馈：写入一条消息（仅限任务）。对话请用 POST /chat。"""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id")
    async with session_scope() as db:
        r = await db.execute(select(Session).where(Session.id == sid))
        s = r.scalar_one_or_none()
        if not s:
            raise HTTPException(status_code=404, detail="Session not found")
        if not _is_task_session(s):
            raise HTTPException(status_code=404, detail="use POST /chat for conversations")
        db.add(
            Message(
                id=uuid.uuid4(),
                session_id=sid,
                role=body.role,
                content=body.message,
            )
        )
        await db.commit()
    # Only direct input when at least one valid role is @-mentioned; else context only.
    mentions = _parse_mentions(body.message)
    valid = await _valid_role_names(mentions)
    if valid:
        async def _reply_and_catch():
            try:
                await _process_task_and_reply(sid, body.message, valid_mentions=valid)
            except Exception as e:
                logger.exception("task_reply_failed", session_id=str(sid), error=str(e))
                async with session_scope() as db:
                    db.add(
                        Message(
                            id=uuid.uuid4(),
                            session_id=sid,
                            role="assistant",
                            content="回复生成失败，请重试或检查配置（对话模型、API Key 等）。",
                        )
                    )
                    await db.commit()
        asyncio.create_task(_reply_and_catch())
    return {"message": "Message sent"}


@router.delete("/chat/room/{session_id}/messages")
async def delete_room_messages(session_id: str) -> dict:
    """清空该任务下的所有群聊消息（仅限任务）。"""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id")
    async with session_scope() as db:
        r = await db.execute(select(Session).where(Session.id == sid))
        s = r.scalar_one_or_none()
        if not s:
            raise HTTPException(status_code=404, detail="Session not found")
        if not _is_task_session(s):
            raise HTTPException(status_code=404, detail="room API is for tasks only")
        await db.execute(delete(Message).where(Message.session_id == sid))
        await db.commit()
    return {"status": "ok", "message": "messages cleared"}
