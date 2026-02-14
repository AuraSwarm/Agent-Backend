"""
AI 员工团队：角色与能力管理 API。

- GET/POST /admin/roles, GET/PUT /admin/roles/{role_name}
- GET /abilities（能力列表，config + 自定义，供管理端绑定与编辑）
- POST/PUT/DELETE /abilities 用于 Web 端增删改自定义能力
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.config.loader import get_config, reload_config, update_default_chat_model
from app.constants import CHAT_ABILITY_ID
from app.storage.db import session_scope
from app.storage.models import CustomAbility, Message, Session
from memory_base.models_team import EmployeeRole, PromptVersion, RoleAbility

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api", tags=["team_admin"])


async def ensure_all_roles_have_chat_ability() -> int:
    """历史角色适配：为所有已有角色补齐必备的「对话」能力，返回本次新增绑定的角色数。"""
    updated = 0
    async with session_scope() as db:
        r = await db.execute(select(EmployeeRole.name))
        role_names = [row[0] for row in r.fetchall()]
        for role_name in role_names:
            r_ab = await db.execute(
                select(RoleAbility).where(
                    RoleAbility.role_name == role_name,
                    RoleAbility.ability_id == CHAT_ABILITY_ID,
                )
            )
            if r_ab.scalar_one_or_none() is None:
                db.add(RoleAbility(role_name=role_name, ability_id=CHAT_ABILITY_ID))
                updated += 1
        await db.commit()
    return updated


def _get_provider_for_model(config: Any, model_id: str) -> tuple[str | None, Any]:
    """根据模型 ID 解析所属 provider，返回 (provider_name, prov) 或 (None, None)。"""
    providers = getattr(config, "chat_providers", {}) or {}
    for name, prov in providers.items():
        models = getattr(prov, "models", None) or [getattr(prov, "model", "")]
        if model_id in (models or []):
            return (name, prov)
    return (None, None)


def _all_provider_model_pairs(config: Any) -> list[tuple[Any, str]]:
    """所有 chat_providers 的 (prov, model_id) 列表，默认 provider 优先，保证 claude-local / cursor-local / copilot-local 等均被包含。"""
    providers = getattr(config, "chat_providers", {}) or {}
    default_name = getattr(config, "default_chat_provider", None) or "dashscope"
    seen: set[str] = set()
    result: list[tuple[Any, str]] = []
    for name in [default_name] + [n for n in providers.keys() if n != default_name]:
        if name not in providers:
            continue
        prov = providers[name]
        for m in getattr(prov, "models", None) or [getattr(prov, "model", "")] or []:
            if m and m not in seen:
                seen.add(m)
                result.append((prov, m))
    return result


def _all_chat_model_ids() -> list[str]:
    """可绑定到角色的对话模型 ID 列表（合并所有 chat_providers 的 models，默认 provider 优先）。"""
    config = get_config()
    providers = getattr(config, "chat_providers", {}) or {}
    default_name = getattr(config, "default_chat_provider", None) or "dashscope"
    seen: set[str] = set()
    result: list[str] = []
    if default_name in providers:
        prov = providers[default_name]
        for m in getattr(prov, "models", None) or [getattr(prov, "model", "")] or []:
            if m and m not in seen:
                seen.add(m)
                result.append(m)
    for name, prov in providers.items():
        if name == default_name:
            continue
        for m in getattr(prov, "models", None) or [getattr(prov, "model", "")] or []:
            if m and m not in seen:
                seen.add(m)
                result.append(m)
    return result


def _allowed_model_ids() -> list[str]:
    """可绑定到角色的对话模型 ID 列表（与 list_models 一致：包含所有 provider 的 models）。"""
    return _all_chat_model_ids()


# --- Schemas ---
class RoleCreate(BaseModel):
    name: str
    description: str | None = None
    status: str = "enabled"
    abilities: list[str] = []
    system_prompt: str = ""
    default_model: str | None = None


class RoleUpdate(BaseModel):
    description: str | None = None
    status: str | None = None
    abilities: list[str] | None = None
    system_prompt: str | None = None
    default_model: str | None = None


class AbilityCreate(BaseModel):
    id: str
    name: str
    description: str = ""
    command: list[str]
    prompt_template: str | None = None


class AbilityUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    command: list[str] | None = None
    prompt_template: str | None = None


def _config_tool_to_item(t: Any) -> dict[str, Any]:
    cmd = getattr(t, "command", None)
    return {
        "id": t.id,
        "name": getattr(t, "name", t.id),
        "description": getattr(t, "description", ""),
        "source": "config",
        "command": cmd if isinstance(cmd, list) else ([cmd] if cmd else []),
        "prompt_template": getattr(t, "prompt_template", None),
    }


def _custom_to_item(row: CustomAbility) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description or "",
        "command": row.command,
        "prompt_template": getattr(row, "prompt_template", None) or None,
        "source": "custom",
    }


@router.get("/models")
async def list_models() -> dict[str, Any]:
    """可绑定到角色的对话模型列表（合并所有 chat_providers 的 models）。配置加载放入线程池避免阻塞事件循环。"""
    config = await asyncio.to_thread(reload_config)
    providers = getattr(config, "chat_providers", {}) or {}
    default_name = getattr(config, "default_chat_provider", None) or "dashscope"
    default_model = None
    if default_name in providers:
        prov = providers[default_name]
        default_model = getattr(prov, "model", None)
    models = _models_list_from_config(config)
    return {"models": models, "default": default_model}


def _models_list_from_config(config: Any) -> list[str]:
    """从已加载的 config 计算模型 ID 列表（供 list_models 在子线程外使用）。"""
    providers = getattr(config, "chat_providers", {}) or {}
    default_name = getattr(config, "default_chat_provider", None) or "dashscope"
    seen: set[str] = set()
    result: list[str] = []
    if default_name in providers:
        prov = providers[default_name]
        for m in getattr(prov, "models", None) or [getattr(prov, "model", "")] or []:
            if m and m not in seen:
                seen.add(m)
                result.append(m)
    for name, prov in providers.items():
        if name == default_name:
            continue
        for m in getattr(prov, "models", None) or [getattr(prov, "model", "")] or []:
            if m and m not in seen:
                seen.add(m)
                result.append(m)
    return result


async def _test_one_model(prov: Any, model: str, prompt: str = "Say OK in one word.") -> tuple[str, bool, str]:
    """Call one model; return (model_id, available, message). On error returns available=False with message."""
    from app.adapters.factory import build_chat_adapter
    adapter = build_chat_adapter(prov, model)
    result = await adapter.call(prompt, model=model)
    out = result[0] if isinstance(result, tuple) else result
    msg = (out or "").strip()[:120]
    return (model, True, msg or "(empty)")


class SetDefaultModelBody(BaseModel):
    model: str


class TestModelsBody(BaseModel):
    """Optional: test only this model_id; omit to test all models of default provider."""
    model: str | None = None


@router.put("/admin/models/default")
async def set_default_model(body: SetDefaultModelBody) -> dict[str, Any]:
    """设置默认对话模型（写入 config/models.yaml 并重载配置）。"""
    allowed = _allowed_model_ids()
    if body.model not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"default model must be one of: {allowed!r}",
        )
    update_default_chat_model(body.model)
    return {"default": body.model}


@router.post("/admin/models/test")
async def test_models_availability(body: TestModelsBody | None = Body(None)) -> dict[str, Any]:
    """测试对话模型可用性。body.model 为空时测试所有 provider 的全部模型（含 claude-local、cursor-local、copilot-local）；指定 model 时仅测试该模型。"""
    config = get_config()
    providers = getattr(config, "chat_providers", {}) or {}
    single_model = None
    if body and getattr(body, "model", None):
        single_model = (body.model or "").strip() or None
    if single_model:
        prov_name, prov = _get_provider_for_model(config, single_model)
        if prov is None:
            raise HTTPException(
                status_code=404,
                detail=f"model {single_model!r} not found in any chat_providers",
            )
        pairs = [(prov, single_model)]
    else:
        if not providers:
            return {"results": [], "message": "no chat provider configured"}
        pairs = _all_provider_model_pairs(config)
    results: list[dict[str, Any]] = []
    prompt = "Say OK in one word."
    for prov, model in pairs:
        try:
            model_id, available, message = await _test_one_model(prov, model, prompt)
        except Exception as e:
            model_id, available, message = model, False, str(e)[:200]
        results.append({"model_id": model_id, "available": available, "message": message})
    return {"results": results}


def _builtin_chat_ability() -> dict[str, Any]:
    """对话能力：每个角色必备，内置不可删改。"""
    return {
        "id": CHAT_ABILITY_ID,
        "name": "对话",
        "description": "与用户进行文字对话",
        "source": "builtin",
        "command": [],
        "prompt_template": None,
    }


@router.get("/abilities")
async def list_abilities() -> list[dict[str, Any]]:
    """列出能力：内置对话 + config local_tools + 自定义（自定义同 id 覆盖）。含 source 与 command（仅 custom）供前端编辑。"""
    config = get_config()
    by_id: dict[str, dict[str, Any]] = {CHAT_ABILITY_ID: _builtin_chat_ability()}
    for t in getattr(config, "local_tools", None) or []:
        by_id[t.id] = _config_tool_to_item(t)
    async with session_scope() as db:
        r = await db.execute(select(CustomAbility))
        for row in r.scalars().all():
            by_id[row.id] = _custom_to_item(row)
    return list(by_id.values())


@router.post("/abilities")
async def create_ability(body: AbilityCreate) -> dict[str, str]:
    """新建自定义能力（Web 端）。"""
    if not body.id.strip():
        raise HTTPException(status_code=400, detail="id is required")
    if body.id.strip() == CHAT_ABILITY_ID:
        raise HTTPException(status_code=400, detail="Ability id 'chat' is reserved for built-in chat ability")
    async with session_scope() as db:
        r = await db.execute(select(CustomAbility).where(CustomAbility.id == body.id.strip()))
        if r.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Ability id already exists")
        db.add(
            CustomAbility(
                id=body.id.strip(),
                name=body.name.strip(),
                description=body.description.strip(),
                command=body.command,
                prompt_template=(body.prompt_template or "").strip() or None,
            )
        )
        await db.commit()
    return {"message": "Ability created"}


@router.get("/abilities/{ability_id}")
async def get_ability(ability_id: str) -> dict[str, Any]:
    """获取单条能力（用于编辑）。内置对话能力只读；config 来源无 command 时前端只读。"""
    if ability_id == CHAT_ABILITY_ID:
        return _builtin_chat_ability()
    config = get_config()
    by_id: dict[str, dict[str, Any]] = {}
    for t in getattr(config, "local_tools", None) or []:
        by_id[t.id] = _config_tool_to_item(t)
    async with session_scope() as db:
        r = await db.execute(select(CustomAbility).where(CustomAbility.id == ability_id))
        row = r.scalar_one_or_none()
        if row:
            by_id[row.id] = _custom_to_item(row)
    if ability_id not in by_id:
        raise HTTPException(status_code=404, detail="Ability not found")
    return by_id[ability_id]


@router.put("/abilities/{ability_id}")
async def update_ability(ability_id: str, body: AbilityUpdate) -> dict[str, str]:
    """更新自定义能力（仅 DB 中的可更新）。内置对话能力不可更新。"""
    if ability_id == CHAT_ABILITY_ID:
        raise HTTPException(status_code=400, detail="Built-in chat ability cannot be updated")
    async with session_scope() as db:
        r = await db.execute(select(CustomAbility).where(CustomAbility.id == ability_id))
        row = r.scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Ability not found (only custom abilities can be updated)")
        if body.name is not None:
            row.name = body.name.strip()
        if body.description is not None:
            row.description = body.description.strip()
        if body.command is not None:
            row.command = body.command
        if body.prompt_template is not None:
            row.prompt_template = (body.prompt_template or "").strip() or None
        await db.commit()
    return {"message": "Ability updated"}


@router.delete("/abilities/{ability_id}")
async def delete_ability(ability_id: str) -> dict[str, str]:
    """删除自定义能力（仅 DB 中的可删除）。内置对话能力不可删除。"""
    if ability_id == CHAT_ABILITY_ID:
        raise HTTPException(status_code=400, detail="Built-in chat ability cannot be deleted")
    async with session_scope() as db:
        r = await db.execute(select(CustomAbility).where(CustomAbility.id == ability_id))
        if not r.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Ability not found (only custom abilities can be deleted)")
        await db.execute(delete(CustomAbility).where(CustomAbility.id == ability_id))
        await db.commit()
    return {"message": "Ability deleted"}


# --- Roles ---
@router.get("/admin/roles")
async def list_roles() -> list[dict[str, Any]]:
    """列出所有 AI 员工角色。"""
    async with session_scope() as db:
        r = await db.execute(select(EmployeeRole))
        roles = list(r.scalars().all())
        out = []
        for role in roles:
            r_ab = await db.execute(select(RoleAbility.ability_id).where(RoleAbility.role_name == role.name))
            abilities = [row[0] for row in r_ab.fetchall()]
            if CHAT_ABILITY_ID not in abilities:
                abilities = [CHAT_ABILITY_ID] + abilities
            out.append({
                "name": role.name,
                "description": role.description or "",
                "status": role.status,
                "abilities": abilities,
                "default_model": getattr(role, "default_model", None),
            })
        return out


@router.get("/admin/roles/{role_name}")
async def get_role(role_name: str) -> dict[str, Any]:
    """获取角色详情（含最新提示词）。"""
    async with session_scope() as db:
        r = await db.execute(select(EmployeeRole).where(EmployeeRole.name == role_name))
        role = r.scalar_one_or_none()
        if not role:
            logger.warning("get_role_not_found", role_name=role_name, role_name_repr=repr(role_name))
            raise HTTPException(status_code=404, detail="Role not found")
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
        return {
            "name": role.name,
            "description": role.description or "",
            "status": role.status,
            "abilities": abilities,
            "system_prompt": latest.content if latest else "",
            "default_model": getattr(role, "default_model", None),
        }


@router.post("/admin/roles")
async def create_role(body: RoleCreate) -> dict[str, str]:
    """创建新角色（含能力绑定、可选绑定模型与初始提示词版本）。"""
    allowed = _allowed_model_ids()
    if body.default_model and body.default_model not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"default_model must be one of: {allowed!r}",
        )
    async with session_scope() as db:
        r = await db.execute(select(EmployeeRole).where(EmployeeRole.name == body.name))
        if r.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Role name already exists")
        role = EmployeeRole(
            name=body.name,
            description=body.description,
            status=body.status,
            default_model=body.default_model or None,
        )
        db.add(role)
        for ability_id in body.abilities:
            db.add(RoleAbility(role_name=body.name, ability_id=ability_id))
        db.add(
            PromptVersion(
                id=f"{body.name}_v1",
                role_name=body.name,
                content=body.system_prompt,
                version=1,
            )
        )
        await db.commit()
    return {"message": "Role created successfully"}


@router.put("/admin/roles/{role_name}")
async def update_role(role_name: str, body: RoleUpdate) -> dict[str, str]:
    """更新角色（描述、状态、能力、绑定模型、提示词）；提示词会新增版本。"""
    if body.default_model is not None:
        allowed = _allowed_model_ids()
        if body.default_model and body.default_model not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"default_model must be one of: {allowed!r}",
            )
    async with session_scope() as db:
        r = await db.execute(select(EmployeeRole).where(EmployeeRole.name == role_name))
        role = r.scalar_one_or_none()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        if body.description is not None:
            role.description = body.description
        if body.status is not None:
            role.status = body.status
        if body.default_model is not None:
            role.default_model = body.default_model or None
        if body.abilities is not None:
            await db.execute(delete(RoleAbility).where(RoleAbility.role_name == role_name))
            for ability_id in body.abilities:
                db.add(RoleAbility(role_name=role_name, ability_id=ability_id))
        if body.system_prompt is not None:
            r_pv = await db.execute(
                select(PromptVersion)
                .where(PromptVersion.role_name == role_name)
                .order_by(PromptVersion.version.desc())
                .limit(1)
            )
            last = r_pv.scalar_one_or_none()
            next_version = (last.version + 1) if last else 1
            db.add(
                PromptVersion(
                    id=f"{role_name}_v{next_version}",
                    role_name=role_name,
                    content=body.system_prompt,
                    version=next_version,
                )
            )
        await db.commit()
    return {"message": "Role updated successfully"}


@router.delete("/admin/roles/{role_name}")
async def delete_role(role_name: str) -> dict[str, str]:
    """删除角色（及其能力绑定与提示词版本）。"""
    async with session_scope() as db:
        r = await db.execute(select(EmployeeRole).where(EmployeeRole.name == role_name))
        role = r.scalar_one_or_none()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        await db.execute(delete(RoleAbility).where(RoleAbility.role_name == role_name))
        await db.execute(delete(PromptVersion).where(PromptVersion.role_name == role_name))
        await db.execute(delete(EmployeeRole).where(EmployeeRole.name == role_name))
        await db.commit()
    return {"message": "Role deleted"}


@router.post("/admin/roles/{role_name}/test")
async def test_role(role_name: str) -> dict[str, Any]:
    """测试角色：基础对话是否可用、各能力是否可执行。不写业务数据，仅用临时 session 测完即删。"""
    from app.routers.team_room import _process_task_and_reply, _try_run_ability

    async with session_scope() as db:
        r = await db.execute(select(EmployeeRole).where(EmployeeRole.name == role_name))
        role = r.scalar_one_or_none()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        r_ab = await db.execute(select(RoleAbility.ability_id).where(RoleAbility.role_name == role_name))
        ability_ids = [row[0] for row in r_ab.fetchall()]
        if CHAT_ABILITY_ID not in ability_ids:
            ability_ids = [CHAT_ABILITY_ID] + ability_ids

    # 1) 基础对话：创建临时任务会话，@ 该角色发一条消息，同步等待回复，检查是否有 assistant 回复
    test_message = "@" + role_name + " 请回复 OK"
    sid: uuid.UUID | None = None
    async with session_scope() as db:
        s = Session(title="[角色测试]", metadata_={"is_task": True})
        db.add(s)
        await db.flush()
        sid = s.id
        db.add(
            Message(
                id=uuid.uuid4(),
                session_id=sid,
                role="user",
                content=test_message,
            )
        )
        await db.commit()
    if sid is None:
        raise RuntimeError("session id not set")
    dialogue_ok = False
    dialogue_message = ""
    await _process_task_and_reply(sid, test_message, valid_mentions=[role_name])
    async with session_scope() as db:
        r = await db.execute(
            select(Message).where(Message.session_id == sid).order_by(Message.created_at.asc())
        )
        messages = list(r.scalars().all())
        assistant_msgs = [m for m in messages if m.role == "assistant"]
        if assistant_msgs:
            dialogue_ok = True
            dialogue_message = (assistant_msgs[-1].content or "").strip()[:200] or "（无文本）"
        await db.execute(delete(Message).where(Message.session_id == sid))
        await db.execute(delete(Session).where(Session.id == sid))
        await db.commit()

    # 2) 能力：对每个绑定能力执行一次简单测试（对话能力已在上一步测过，可跳过或标为通过）
    ability_results: list[dict[str, Any]] = []
    for aid in ability_ids:
        if aid == CHAT_ABILITY_ID:
            ability_results.append({"id": aid, "ok": dialogue_ok, "message": "与基础对话共用测试"})
            continue
        result = await _try_run_ability(aid, ability_ids, "test")
        is_error = isinstance(result, str) and (
            result.strip().startswith("[") or "失败" in result or "异常" in result
        )
        ok = not is_error
        msg = (result or "(无输出)").strip()[:150] if result else "(无输出)"
        ability_results.append({"id": aid, "ok": ok, "message": msg})
    return {
        "dialogue_ok": dialogue_ok,
        "dialogue_message": dialogue_message,
        "abilities": ability_results,
    }


@router.post("/admin/roles/ensure-chat-ability")
async def ensure_chat_ability_for_all_roles() -> dict[str, Any]:
    """历史角色适配：为所有已有角色补齐必备的「对话」能力（未绑定则写入）。返回本次新增绑定的角色数。"""
    updated = await ensure_all_roles_have_chat_ability()
    return {"message": "OK", "updated": updated}


@router.post("/admin/migrate-prompt-template")
async def migrate_prompt_template() -> dict[str, Any]:
    """单次修复：为 custom_abilities 表补齐 prompt_template 列（幂等，可重复执行）。供 Web 端「修复」按钮或脚本调用。"""
    from app.storage.db import run_migrate_prompt_template
    await run_migrate_prompt_template()
    return {"message": "OK", "detail": "custom_abilities.prompt_template 已就绪"}
