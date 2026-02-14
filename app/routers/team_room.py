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
from typing import Any

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


async def _get_task_room_roles(session_id: uuid.UUID) -> list[str]:
    """返回该任务群聊的参与角色列表（来自 session metadata assignee_roles），用于作为上下文告知对话中的角色。"""
    async with session_scope() as db:
        r = await db.execute(select(Session).where(Session.id == session_id))
        s = r.scalar_one_or_none()
        if not s or not _is_task_session(s):
            return []
        meta = getattr(s, "metadata_", None) or {}
        return _task_meta_assignee_roles(meta)


def _role_reply_failure_message(role_name: str) -> str:
    """某角色回复失败时写入群聊的提示文案，建议检查模型可用性等。"""
    return (
        f"【{role_name}】回复失败。建议检查：模型可用性、API Key 及网络配置；"
        "或在「员工角色管理」中对该角色使用「测试」功能排查。"
    )


# 多角色协同上下文：各角色只扮演自己、与其他人真实协作的约定与交互方式（便于 claude-local 等模型理解 @ 的用法）
ROOM_COLLABORATIVE_HOWTO = """
【角色边界】
- 你只扮演当前分配给你的角色，不代其他角色发言、不假设或虚构其他角色的回复；其他角色由各自独立回复，与你是真实的协作关系。
- 回复时仅代表本角色立场与能力。

【@ 的交互模式（重要）】
- 本群聊通过 @角色名 来指定由谁回复。当用户消息里包含 @某角色 时，该角色会收到并回复。
- 当你的回复需要其他角色参与（补充、执行子任务、确认或转交）时，你必须在回复正文中写出 @角色名（例如 @张三、@产品经理），这样用户或系统会在下一轮让被 @ 的角色回复。不要只用自然语言说「请张三来」或「让李四负责」而不写 @角色名，否则无法触发该角色回复。
- 格式要求：在需要邀请某角色时，在句中直接写 @角色名。正确示例：「这部分建议由 @开发 实现。」「@产品 能否确认一下需求？」错误示例：「请开发实现」（未写 @开发）、「让产品确认」（未写 @产品）。
- 控制 @ 的次数：仅在确实需要对方参与时才 @；能独立回答或只是打招呼时不必 @。需要多人参与时可写多个 @角色名。"""


async def _get_task_room_roles_with_descriptions(session_id: uuid.UUID) -> list[tuple[str, str]]:
    """返回该任务群聊的参与角色列表及每人描述 [(name, description), ...]，用于构建协同上下文。"""
    names = await _get_task_room_roles(session_id)
    if not names:
        return []
    async with session_scope() as db:
        r = await db.execute(
            select(EmployeeRole.name, EmployeeRole.description).where(EmployeeRole.name.in_(names))
        )
        rows = r.fetchall()
    by_name: dict[str, str] = {}
    for row in rows:
        name = row[0] if row else ""
        desc = (row[1] or "").strip() if len(row) > 1 else ""
        if name:
            by_name[name] = desc
    return [(n, by_name.get(n, "")) for n in names]


def _build_room_collaborative_context(roles_with_descriptions: list[tuple[str, str]]) -> str:
    """根据「角色名 + 描述」列表构建多角色协同上下文：各角色只扮演自己，与其他人为真实协作关系；并明确可 @ 的角色名供模型原样使用。"""
    if not roles_with_descriptions:
        return ""
    names = [n for n, _ in roles_with_descriptions]
    lines = [
        "【当前任务协同角色】以下角色共同参与本任务，各角色只扮演自己，与彼此为真实协作关系（由各自独立回复，不代他人发言）："
    ]
    for name, desc in roles_with_descriptions:
        if desc:
            lines.append(f"- {name}：{desc}")
        else:
            lines.append(f"- {name}")
    lines.append("【可 @ 的角色名】在回复中需要邀请某角色参与时，请原样使用以下名称并加上 @ 前缀，例如 @" + (names[0] if names else "角色名") + "：")
    lines.append("、".join(names) + "。")
    lines.append(ROOM_COLLABORATIVE_HOWTO.strip())
    return "\n".join(lines)


def _extract_path_from_count_folders_intent(text: str) -> str | None:
    # 支持「多少文件夹」「多少个文件夹」及 "how many folders" 等
    has_intent = (
        "多少文件夹" in text
        or "多少个文件夹" in text
        or ("folder" in text.lower() and ("多少" in text or "how many" in text.lower()))
    )
    if not has_intent:
        return None
    # 支持 "检查 /path 下有多少文件夹" 或 "检查/path下有多少文件夹"（下可紧贴路径）
    m = re.search(r"检查\s*([^\s]+?)\s*下\s*有多少个?文件夹", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"检查\s*(/[^\s]*?)\s*下\s*有多少个?文件夹", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"检查\s*(/[^\s]*)\s*.*多少个?文件夹", text)
    if m:
        return m.group(1).strip()
    # 英文：how many folders (in|under) /path
    m = re.search(r"how\s+many\s+folders?\s+(?:in|under)\s+([^\s]+)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    if "下" in text:
        m = re.search(r"检查\s*([^\s]+?)\s*下\s*有多少", text)
        if m:
            return m.group(1).strip()
    return None


def _run_count_folders(path: str) -> str:
    """执行「数文件夹」逻辑，返回结果文案或错误信息。"""
    p = Path(path)
    if not p.exists():
        return "Path not found: " + path
    if not p.is_dir():
        return "Not a directory: " + path
    count = sum(1 for _ in p.iterdir() if _.is_dir())
    return "Directory " + path + " has " + str(count) + " folder(s)."


def _process_task_message(message_text: str) -> str:
    path = _extract_path_from_count_folders_intent(message_text)
    if path is None:
        return "当前支持：根据「检查某路径下有多少文件夹」类问题直接执行并回复结果；其他问题请直接对话，我会尽量回答。示例：检查 /tmp 下有多少文件夹。"
    return _run_count_folders(path)


async def _get_role_prompt_and_abilities(role_name: str) -> tuple[str, list[str], str | None, str]:
    """Load role's latest system prompt, ability ids, default_model, and role description. Returns ('', [], None, '') if role not found."""
    async with session_scope() as db:
        r = await db.execute(select(EmployeeRole).where(EmployeeRole.name == role_name))
        role = r.scalar_one_or_none()
        if not role:
            return ("", [], None, "")
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
        role_description = (role.description or "").strip()
        return (system_prompt, abilities, default_model, role_description)


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
    from app.adapters.factory import build_chat_adapter

    prompt = prompt_template.replace("{message}", user_message).replace("{user_message}", user_message)
    config = get_config()
    chat_providers = getattr(config, "chat_providers", {}) or {}
    default_chat = getattr(config, "default_chat_provider", None) or "dashscope"
    if default_chat not in chat_providers:
        return "[提示词能力需要配置 chat_providers]"
    prov = chat_providers[default_chat]
    _get = lambda p, k, d=None: getattr(p, k, d) if not isinstance(p, dict) else p.get(k, d)
    model = _get(prov, "model") or ("qwen-max" if not (_get(prov, "models")) else (_get(prov, "models") or [])[0])
    adapter = build_chat_adapter(prov, model or "qwen-max")
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


async def _build_ability_list_context(ability_ids: list[str]) -> str:
    """将能力 id 列表解析为名称与描述，拼成供对话上下文使用的能力清单文本。"""
    if not ability_ids:
        return ""
    lines = []
    for aid in ability_ids:
        ab = await _get_ability_by_id(aid)
        name = (ab.get("name") or ab.get("id") or aid) if ab else aid
        desc = (ab.get("description") or "").strip() if ab else ""
        lines.append(f"- {name}（{aid}）" + (f": {desc}" if desc else ""))
    return "\n".join(lines)


def _resolve_provider_for_model(config: Any, model_id: str) -> tuple[str | None, Any]:
    """根据模型 ID 解析所属 chat_provider，返回 (provider_name, prov) 或 (None, None)。"""
    providers = getattr(config, "chat_providers", {}) or {}
    for name, prov in providers.items():
        models = getattr(prov, "models", None) or [getattr(prov, "model", "")]
        if model_id in (models or []):
            return (name, prov)
    return (None, None)


async def _role_reply_via_chat(
    session_id: uuid.UUID,
    role_name: str,
    message_text: str,
    system_prompt: str,
    ability_ids: list[str],
    default_model: str | None,
    tool_result_prefix: str | None,
    role_description: str = "",
    room_role_names: list[str] | None = None,
    room_collaborative_context: str | None = None,
) -> str | None:
    """Call chat API with role context (角色提示词、能力清单、群聊协同角色等接入 system)；return assistant text or None on failure."""
    from app.adapters.factory import build_chat_adapter

    config = get_config()
    chat_providers = getattr(config, "chat_providers", {}) or {}
    default_chat = getattr(config, "default_chat_provider", None) or "dashscope"
    if default_chat not in chat_providers:
        return None
    _get = lambda p, k, d=None: getattr(p, k, d) if not isinstance(p, dict) else p.get(k, d)
    default_prov = chat_providers[default_chat]
    model = default_model or _get(default_prov, "model")
    if not model:
        models_list = _get(default_prov, "models") or []
        model = models_list[0] if models_list else "qwen-max"
    prov_name, prov = _resolve_provider_for_model(config, model)
    if prov_name is None:
        prov_name, prov = default_chat, default_prov
    adapter = build_chat_adapter(prov, model or "qwen-max")
    parts = []
    parts.append(f"【角色】{role_name}" + (f"。{role_description}" if role_description else "。"))
    if room_collaborative_context and room_collaborative_context.strip():
        parts.append(room_collaborative_context.strip())
    elif room_role_names:
        parts.append(
            "【当前群聊参与角色】本任务中参与对话的角色有：" + "、".join(room_role_names)
            + "。你只扮演本角色，与上述角色为真实协作关系（他们由各自独立回复）。需要某角色参与时，在回复中必须写出 @角色名（如 @"
            + (room_role_names[0] if room_role_names else "角色名")
            + "），否则无法触发该角色回复。"
        )
    parts.append("【系统提示词】\n" + (system_prompt.strip() or "请根据用户消息友好回复；若涉及你具备的能力可说明或执行。"))
    ability_list_text = await _build_ability_list_context(ability_ids)
    if ability_list_text:
        parts.append(
            "【能力清单】\n"
            + ability_list_text
            + "\n（当用户要求执行某能力时再调用。若用户只是打招呼或一般对话，请正常友好回复，勿回复「Unsupported task」。）"
        )
    system = "\n\n".join(parts)
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


async def _reply_for_one_role(
    session_id: uuid.UUID,
    role_name: str,
    message_text: str,
    room_role_names: list[str],
    room_collaborative_context: str,
) -> str:
    """单角色生成回复（供多角色并发调用）。返回该角色的回复内容。"""
    system_prompt, ability_ids, default_model, role_description = await _get_role_prompt_and_abilities(role_name)
    tool_result_prefix: str | None = None
    ability_id, remainder = _parse_execute_intent(message_text)
    if ability_id and ability_ids:
        tool_result_prefix = await _try_run_ability(ability_id, ability_ids, remainder)
    if tool_result_prefix is None:
        path = _extract_path_from_count_folders_intent(message_text)
        if path is not None:
            tool_result_prefix = _run_count_folders(path)
    chat_reply = await _role_reply_via_chat(
        session_id,
        role_name,
        message_text,
        system_prompt,
        ability_ids,
        default_model,
        tool_result_prefix,
        role_description,
        room_role_names,
        room_collaborative_context,
    )
    if chat_reply is not None:
        return chat_reply
    return _role_reply_failure_message(role_name)


async def _process_task_and_reply(
    session_id: uuid.UUID, message_text: str, valid_mentions: list[str] | None = None
) -> None:
    """为每个被 @ 的有效角色各生成一条 assistant 回复（多人时并发执行，避免轮询超时）。"""
    valid_mentions = valid_mentions or []
    if not valid_mentions:
        async with session_scope() as db:
            db.add(
                Message(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    role="assistant",
                    content="已记录为上下文。如需某角色回复，请 @ 该角色。",
                )
            )
            await db.commit()
        return
    room_role_names = await _get_task_room_roles(session_id)
    room_roles_with_descriptions = await _get_task_room_roles_with_descriptions(session_id)
    room_collaborative_context = _build_room_collaborative_context(room_roles_with_descriptions)
    tasks = [
        _reply_for_one_role(
            session_id, role_name, message_text, room_role_names, room_collaborative_context
        )
        for role_name in valid_mentions
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    replies: list[str] = []
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            logger.warning("task_role_reply_error", role=valid_mentions[i], error=str(r))
            replies.append(_role_reply_failure_message(valid_mentions[i]))
        else:
            replies.append(r)
    async with session_scope() as db:
        for reply in replies:
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
# 支持角色名含空格（如 Claude Analyst）、连字符（如 Qwen-deep Analyst）
MENTION_PATTERN = re.compile(r"@([a-zA-Z0-9_\-]+(?:\s[a-zA-Z0-9_\-]+)*)")


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
        if m.role == "assistant":
            # 找到前一条 user 消息，并统计该 user 之后连续几条 assistant（含当前）
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
                    # 当前是第 k+1 条连续 assistant（从 0 起为第 k 条）
                    pos = k
                    if pos < len(prev_mentions):
                        item["reply_by_role"] = prev_mentions[pos]
                    else:
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
                            content="回复生成失败。建议检查模型可用性、API Key 及网络配置，或在「员工角色管理」中对该角色使用「测试」功能排查。",
                        )
                    )
                    await db.commit()
        asyncio.create_task(_reply_and_catch())
    elif mentions:
        # 有 @ 但无匹配角色：写入一条说明，避免页面一直等不到回复
        async with session_scope() as db:
            db.add(
                Message(
                    id=uuid.uuid4(),
                    session_id=sid,
                    role="assistant",
                    content="未找到可回复的角色。请确认 @ 的角色名与「员工角色管理」中的名称完全一致（含空格、大小写、连字符）。",
                )
            )
            await db.commit()
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
