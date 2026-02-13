"""
AI 员工团队：角色与能力管理 API。

- GET/POST /admin/roles, GET/PUT /admin/roles/{role_name}
- GET /abilities（能力列表，config + 自定义，供管理端绑定与编辑）
- POST/PUT/DELETE /abilities 用于 Web 端增删改自定义能力
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.config.loader import get_config
from app.storage.db import session_scope
from app.storage.models import CustomAbility
from memory_base.models_team import EmployeeRole, PromptVersion, RoleAbility

router = APIRouter(prefix="/api", tags=["team_admin"])


# --- Schemas ---
class RoleCreate(BaseModel):
    name: str
    description: str | None = None
    status: str = "enabled"
    abilities: list[str] = []
    system_prompt: str = ""


class RoleUpdate(BaseModel):
    description: str | None = None
    status: str | None = None
    abilities: list[str] | None = None
    system_prompt: str | None = None


class AbilityCreate(BaseModel):
    id: str
    name: str
    description: str = ""
    command: list[str]


class AbilityUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    command: list[str] | None = None


def _config_tool_to_item(t: Any) -> dict[str, Any]:
    return {
        "id": t.id,
        "name": getattr(t, "name", t.id),
        "description": getattr(t, "description", ""),
        "source": "config",
    }


def _custom_to_item(row: CustomAbility) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description or "",
        "command": row.command,
        "source": "custom",
    }


@router.get("/abilities")
async def list_abilities() -> list[dict[str, Any]]:
    """列出能力：config local_tools + 自定义（自定义同 id 覆盖）。含 source 与 command（仅 custom）供前端编辑。"""
    config = get_config()
    by_id: dict[str, dict[str, Any]] = {}
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
            )
        )
        await db.commit()
    return {"message": "Ability created"}


@router.get("/abilities/{ability_id}")
async def get_ability(ability_id: str) -> dict[str, Any]:
    """获取单条能力（用于编辑）。config 来源无 command 时前端只读。"""
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
    """更新自定义能力（仅 DB 中的可更新）。"""
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
        await db.commit()
    return {"message": "Ability updated"}


@router.delete("/abilities/{ability_id}")
async def delete_ability(ability_id: str) -> dict[str, str]:
    """删除自定义能力（仅 DB 中的可删除）。"""
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
            out.append({
                "name": role.name,
                "description": role.description or "",
                "status": role.status,
                "abilities": abilities,
            })
        return out


@router.get("/admin/roles/{role_name}")
async def get_role(role_name: str) -> dict[str, Any]:
    """获取角色详情（含最新提示词）。"""
    async with session_scope() as db:
        r = await db.execute(select(EmployeeRole).where(EmployeeRole.name == role_name))
        role = r.scalar_one_or_none()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        r_ab = await db.execute(select(RoleAbility.ability_id).where(RoleAbility.role_name == role_name))
        abilities = [row[0] for row in r_ab.fetchall()]
        r_pv = await db.execute(
            select(PromptVersion).where(PromptVersion.role_name == role_name).order_by(PromptVersion.version.desc())
        )
        latest = r_pv.scalar_one_or_none()
        return {
            "name": role.name,
            "description": role.description or "",
            "status": role.status,
            "abilities": abilities,
            "system_prompt": latest.content if latest else "",
        }


@router.post("/admin/roles")
async def create_role(body: RoleCreate) -> dict[str, str]:
    """创建新角色（含能力绑定与初始提示词版本）。"""
    async with session_scope() as db:
        r = await db.execute(select(EmployeeRole).where(EmployeeRole.name == body.name))
        if r.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Role name already exists")
        role = EmployeeRole(
            name=body.name,
            description=body.description,
            status=body.status,
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
    """更新角色（描述、状态、能力、提示词）；提示词会新增版本。"""
    async with session_scope() as db:
        r = await db.execute(select(EmployeeRole).where(EmployeeRole.name == role_name))
        role = r.scalar_one_or_none()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        if body.description is not None:
            role.description = body.description
        if body.status is not None:
            role.status = body.status
        if body.abilities is not None:
            await db.execute(delete(RoleAbility).where(RoleAbility.role_name == role_name))
            for ability_id in body.abilities:
                db.add(RoleAbility(role_name=role_name, ability_id=ability_id))
        if body.system_prompt is not None:
            r_pv = await db.execute(
                select(PromptVersion).where(PromptVersion.role_name == role_name).order_by(PromptVersion.version.desc())
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
