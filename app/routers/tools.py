"""Local tools list and execute."""

import asyncio
import subprocess

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config.loader import get_config
from app.tools.runner import execute_local_tool, get_registered_tools

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolExecuteRequest(BaseModel):
    tool_id: str
    params: dict[str, str] | None = None


@router.get("")
async def list_tools() -> list[dict[str, str]]:
    """List registered local tools (id, name, description)."""
    config = get_config()
    return get_registered_tools(config)


@router.post("/execute")
async def run_tool(req: ToolExecuteRequest) -> dict:
    """Execute a registered local tool. Returns stdout, stderr, returncode."""
    config = get_config()
    params = {k: str(v) for k, v in (req.params or {}).items()}
    try:
        result = await asyncio.to_thread(execute_local_tool, config, req.tool_id, params)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="tool execution timeout") from None
