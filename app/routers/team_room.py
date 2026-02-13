"""
AI 员工团队：任务列表与群聊通道 API。

- GET /api/tasks：任务列表（基于 sessions）
- GET /api/chat/room/{session_id}/messages：群聊消息列表
- POST /api/chat/room/{session_id}/message：用户反馈（写入即触发）
"""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, insert

from app.storage.db import session_scope
from app.storage.models import Message, Session

router = APIRouter(prefix="/api", tags=["team_room"])


class RoomMessageBody(BaseModel):
    role: str = "user"
    message: str
    message_type: str = "user_message"


@router.get("/tasks")
async def get_tasks(limit: int = 50) -> list[dict]:
    """任务列表（来自 sessions：id、title、status、last_updated）。"""
    limit = min(limit, 100)
    async with session_scope() as db:
        r = await db.execute(
            select(Session)
            .where(Session.status == 1)
            .order_by(Session.updated_at.desc())
            .limit(limit)
        )
        sessions = list(r.scalars().all())
    return [
        {
            "id": str(s.id),
            "title": s.title or "未命名任务",
            "status": "completed" if s.status != 1 else "in_progress",
            "last_updated": s.updated_at.isoformat() if s.updated_at else "",
        }
        for s in sessions
    ]


@router.get("/chat/room/{session_id}/messages")
async def get_room_messages(session_id: str) -> list[dict]:
    """群聊消息列表（role、message、timestamp）。"""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id")
    async with session_scope() as db:
        r = await db.execute(
            select(Message).where(Message.session_id == sid).order_by(Message.created_at.asc())
        )
        messages = list(r.scalars().all())
    return [
        {
            "role": m.role,
            "message": m.content,
            "timestamp": m.created_at.isoformat() if m.created_at else "",
        }
        for m in messages
    ]


@router.post("/chat/room/{session_id}/message")
async def post_room_message(session_id: str, body: RoomMessageBody) -> dict:
    """用户反馈：写入一条消息（输入即触发，后续可在此挂任务拆解与 AI 回复）。"""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id")
    async with session_scope() as db:
        r = await db.execute(select(Session).where(Session.id == sid))
        if not r.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Session not found")
        db.add(
            Message(
                id=uuid.uuid4(),
                session_id=sid,
                role=body.role,
                content=body.message,
            )
        )
        await db.commit()
    return {"message": "Message sent"}


@router.delete("/chat/room/{session_id}/messages")
async def delete_room_messages(session_id: str) -> dict:
    """清空该任务下的所有群聊消息。"""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id")
    async with session_scope() as db:
        r = await db.execute(select(Session).where(Session.id == sid))
        if not r.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Session not found")
        await db.execute(delete(Message).where(Message.session_id == sid))
        await db.commit()
    return {"status": "ok", "message": "messages cleared"}
