"""Sessions CRUD, messages, and semantic search."""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, text

from app.embedding.engine import get_embedding
from app.storage.db import get_session_factory, log_audit, session_scope
from app.storage.models import Message, Session, SessionSummary

router = APIRouter(prefix="/sessions", tags=["sessions"])

SESSION_PREVIEW_LEN = 80


def _truncate_preview(text: str | None, max_len: int = SESSION_PREVIEW_LEN) -> str | None:
    if not text or not text.strip():
        return None
    s = text.strip().replace("\n", " ")[:max_len]
    return s + "…" if len(text.strip()) > max_len else s


# --- Schemas ---
class CreateSessionRequest(BaseModel):
    title: str | None = None


class CreateSessionResponse(BaseModel):
    session_id: str


class SessionListItem(BaseModel):
    session_id: str
    title: str | None
    updated_at: str
    first_message_preview: str | None = None
    last_message_preview: str | None = None
    topic_summary: str | None = None


class MessageItem(BaseModel):
    role: str
    content: str


class UpdateSessionRequest(BaseModel):
    title: str | None = None


# --- Routes ---
@router.post("", response_model=CreateSessionResponse)
async def create_session(body: CreateSessionRequest | None = None) -> CreateSessionResponse:
    """Create a new chat session."""
    async with session_scope() as session:
        s = Session(title=body.title if body else None)
        session.add(s)
        await session.flush()
        return CreateSessionResponse(session_id=str(s.id))


@router.get("", response_model=list[SessionListItem])
async def list_sessions(limit: int = 50) -> list[SessionListItem]:
    """List recent sessions with first/last message preview and topic summary."""
    limit = min(limit, 100)
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(
            select(Session)
            .where(Session.status == 1)
            .order_by(Session.updated_at.desc())
            .limit(limit)
        )
        sessions = r.scalars().all()
    if not sessions:
        return []
    ids = [s.id for s in sessions]

    first_map: dict[uuid.UUID, str] = {}
    last_map: dict[uuid.UUID, str] = {}
    summary_map: dict[uuid.UUID, str] = {}
    async with factory() as db:
        r_msg = await db.execute(
            select(Message.session_id, Message.content, Message.created_at)
            .where(Message.session_id.in_(ids))
            .order_by(Message.session_id, Message.created_at.asc())
        )
        for sid, content, _ in r_msg.fetchall():
            if sid not in first_map:
                first_map[sid] = content or ""
            last_map[sid] = content or ""

        r_sum = await db.execute(
            select(SessionSummary.session_id, SessionSummary.summary_text, SessionSummary.created_at)
            .where(SessionSummary.session_id.in_(ids))
            .order_by(SessionSummary.session_id, SessionSummary.created_at.desc())
        )
        for sid, summary_text, _ in r_sum.fetchall():
            if sid not in summary_map and summary_text:
                summary_map[sid] = summary_text

    return [
        SessionListItem(
            session_id=str(s.id),
            title=s.title,
            updated_at=s.updated_at.isoformat() if s.updated_at else "",
            first_message_preview=_truncate_preview(first_map.get(s.id)),
            last_message_preview=_truncate_preview(last_map.get(s.id)),
            topic_summary=_truncate_preview(summary_map.get(s.id), max_len=120) if summary_map.get(s.id) else None,
        )
        for s in sessions
    ]


@router.get("/{session_id}/messages", response_model=list[MessageItem])
async def get_session_messages(session_id: str) -> list[MessageItem]:
    """Get messages for a session."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="session not found")
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(
            select(Message).where(Message.session_id == sid).order_by(Message.created_at.asc())
        )
        messages = r.scalars().all()
    return [MessageItem(role=m.role, content=m.content or "") for m in messages]


@router.patch("/{session_id}")
async def update_session(session_id: str, body: UpdateSessionRequest) -> dict:
    """Update session (e.g. title)."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="session not found")
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(select(Session).where(Session.id == sid))
        s = r.scalar_one_or_none()
        if not s:
            raise HTTPException(status_code=404, detail="session not found")
        if body.title is not None:
            s.title = body.title
        await db.commit()
    return {"status": "ok"}


@router.delete("/{session_id}")
async def delete_session(session_id: str) -> dict:
    """Delete a session and its messages/summaries."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="session not found")
    async with session_scope() as db:
        r = await db.execute(select(Session).where(Session.id == sid))
        s = r.scalar_one_or_none()
        if not s:
            raise HTTPException(status_code=404, detail="session not found")
        await db.execute(delete(Session).where(Session.id == sid))
        await log_audit(db, "delete_session", "session", details={"session_id": session_id})
    return {"status": "ok", "message": "session deleted"}


@router.get("/{session_id}/search")
async def session_search(session_id: str, query: str, limit: int = 5) -> dict:
    """Semantic search over session messages (embedding similarity)."""
    vec = await get_embedding(query)
    if not vec:
        return {"matches": []}
    # vec is from get_embedding (list of floats); format as PG vector literal for parameterized query
    vec_str = "[" + ",".join(str(x) for x in vec) + "]"
    factory = get_session_factory()
    async with factory() as session:
        r = await session.execute(
            text("""
                SELECT id, role, content, 1 - (embedding <=> :vec::vector) AS score
                FROM messages WHERE session_id = :sid AND embedding IS NOT NULL
                ORDER BY embedding <=> :vec::vector
                LIMIT :lim
            """),
            {"sid": session_id, "vec": vec_str, "lim": limit},
        )
        rows = r.fetchall()
    return {
        "matches": [
            {"id": str(row[0]), "role": row[1], "content": row[2], "score": float(row[3])}
            for row in rows
        ]
    }
