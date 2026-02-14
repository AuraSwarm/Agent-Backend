"""Local tools list and execute (config + custom abilities from DB)."""

import asyncio
import subprocess

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.config.loader import get_config
from app.storage.db import session_scope
from app.storage.models import CustomAbility
from app.tools.runner import execute_local_tool, get_registered_tools

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolExecuteRequest(BaseModel):
    tool_id: str
    params: dict[str, str] | None = None


async def _load_custom_abilities() -> list[dict]:
    """Load custom abilities from DB for merge with config."""
    async with session_scope() as db:
        r = await db.execute(select(CustomAbility))
        return [
            {"id": row.id, "name": row.name, "description": row.description or "", "command": row.command}
            for row in r.scalars().all()
        ]


@router.get("")
async def list_tools() -> list[dict[str, str]]:
    """List registered local tools (config + custom)."""
    config = get_config()
    custom = await _load_custom_abilities()
    return get_registered_tools(config, custom)


@router.post("/execute")
async def run_tool(req: ToolExecuteRequest) -> dict:
    """Execute a registered local tool (config + custom). Returns stdout, stderr, returncode."""
    config = get_config()
    custom = await _load_custom_abilities()
    params = {k: str(v) for k, v in (req.params or {}).items()}
    try:
        result = await asyncio.to_thread(
            execute_local_tool, config, req.tool_id, params, 30, custom
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="tool execution timeout") from None
