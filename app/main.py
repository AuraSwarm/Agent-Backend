"""
FastAPI gateway: /health, /admin/reload, /chat (SSE/stream), /sessions, /models, static web UI.
"""

import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config.loader import get_config, reload_config, start_config_watcher, validate_required_env
from app.embedding.engine import get_embedding
from sqlalchemy import select, text
from sqlalchemy import insert
from app.storage.db import get_session_factory, init_db, log_audit, session_scope
from app.storage.models import Message, Session, SessionSummary
from app.adapters.cloud import CloudAPIAdapter
from sqlalchemy import delete


async def _persist_chat_messages(session_id: str, user_content: str, assistant_content: str) -> None:
    """Save user and assistant messages to DB for conversation history."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return
    async with session_scope() as db:
        await db.execute(
            insert(Message).values(
                id=uuid.uuid4(),
                session_id=sid,
                role="user",
                content=user_content,
            )
        )
        await db.execute(
            insert(Message).values(
                id=uuid.uuid4(),
                session_id=sid,
                role="assistant",
                content=assistant_content,
            )
        )
        r = await db.execute(select(Message).where(Message.session_id == sid).order_by(Message.created_at.asc()))
        first_msg_only = r.scalars().first()
        if first_msg_only and first_msg_only.role == "user":
            r2 = await db.execute(select(Session).where(Session.id == sid))
            s = r2.scalar_one_or_none()
            if s and not (s.title and s.title.strip()):
                title = (user_content[:200] + "…") if len(user_content) > 200 else user_content
                s.title = title or "新对话"
        await db.commit()

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: validate env, init DB, optional config watcher. Shutdown: close."""
    logger.info("startup_start")
    validate_required_env()
    logger.info("env_validated")
    try:
        logger.info("init_db_start")
        await init_db()
        logger.info("init_db_done")
    except (ConnectionRefusedError, OSError) as e:
        if getattr(e, "errno", None) == 111 or "connection refused" in str(e).lower():
            from app.config.loader import get_app_settings
            settings = get_app_settings()
            url = settings.database_url
            host_part = url.split("@", 1)[1].split("/")[0] if "@" in url else "localhost:5432"
            logger.error("postgres_connection_refused", detail=str(e), host_port=host_part)
            raise SystemExit(
                f"Cannot connect to PostgreSQL at {host_part}. "
                "Fix the database before starting: systemctl start postgresql (or brew services start postgresql), or ./run docker. "
                "Debug: pg_isready -h <host> -p <port> or systemctl status postgresql."
            ) from e
        raise

    def on_config_change(_event: str, path: str) -> None:
        reload_config()
        logger.info("config_reloaded", path=path)

    start_config_watcher(on_config_change)
    logger.info("config_watcher_started")
    logger.info("application_ready")
    yield
    logger.info("shutdown_start")
    # Shutdown: nothing to close for now


app = FastAPI(title="Agent Backend", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log each request (method, path, status, duration) while running."""
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = (time.monotonic() - start) * 1000
    logger.info(
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round(duration_ms, 2),
    )
    return response


# --- Models (for web UI) ---
@app.get("/models")
async def list_models() -> dict:
    """Return available chat model ids from config."""
    config = get_config()
    providers = getattr(config, "chat_providers", {}) or {}
    default_name = config.default_chat_provider or "dashscope"
    if default_name not in providers:
        return {"models": [], "default": None}
    prov = providers[default_name]
    models = getattr(prov, "models", None) or [prov.model]
    return {"models": models, "default": prov.model}


# --- Health ---
@app.get("/health")
async def health() -> dict:
    """DB and Redis status for readiness probe."""
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}


# --- Admin ---
@app.post("/admin/reload")
async def admin_reload() -> dict:
    """Hot reload config from disk."""
    reload_config()
    async with session_scope() as session:
        await log_audit(session, "reload_config", "config", details={"source": "admin"})
    return {"status": "ok", "message": "config reloaded"}


# --- Sessions ---
class CreateSessionRequest(BaseModel):
    title: str | None = None


class CreateSessionResponse(BaseModel):
    session_id: str


@app.post("/sessions", response_model=CreateSessionResponse)
async def create_session(body: CreateSessionRequest | None = None) -> CreateSessionResponse:
    """Create a new chat session."""
    async with session_scope() as session:
        s = Session(title=body.title if body else None)
        session.add(s)
        await session.flush()
        return CreateSessionResponse(session_id=str(s.id))


# Preview length for three-part conversation label (first message + last message + topic summary)
SESSION_PREVIEW_LEN = 80


def _truncate_preview(text: str | None, max_len: int = SESSION_PREVIEW_LEN) -> str | None:
    if not text or not text.strip():
        return None
    s = text.strip().replace("\n", " ")[:max_len]
    return s + "…" if len(text.strip()) > max_len else s


class SessionListItem(BaseModel):
    """Session list item with three-part label: first message, last message, topic summary."""

    session_id: str
    title: str | None
    updated_at: str
    first_message_preview: str | None = None
    last_message_preview: str | None = None
    topic_summary: str | None = None


@app.get("/sessions", response_model=list[SessionListItem])
async def list_sessions(limit: int = 50) -> list[SessionListItem]:
    """List recent sessions (for conversation sidebar). Each item includes first/last message preview and topic summary."""
    limit = min(limit, 100)
    factory = get_session_factory()
    async with factory() as db:
        q = (
            select(Session)
            .where(Session.status == 1)
            .order_by(Session.updated_at.desc())
            .limit(limit)
        )
        r = await db.execute(q)
        sessions = r.scalars().all()
    if not sessions:
        return []
    ids = [s.id for s in sessions]

    # First message, last message, and latest topic summary per session (aggregate in Python)
    first_map: dict[uuid.UUID, str] = {}
    last_map: dict[uuid.UUID, str] = {}
    summary_map: dict[uuid.UUID, str] = {}
    async with factory() as db:
        r_msg = await db.execute(
            select(Message.session_id, Message.content, Message.created_at)
            .where(Message.session_id.in_(ids))
            .order_by(Message.session_id, Message.created_at.asc())
        )
        rows_msg = r_msg.fetchall()
        for sid, content, _ in rows_msg:
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


class MessageItem(BaseModel):
    role: str
    content: str


@app.get("/sessions/{session_id}/messages", response_model=list[MessageItem])
async def get_session_messages(session_id: str) -> list[MessageItem]:
    """Get messages for a session (for restoring conversation context)."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="session not found")
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(
            select(Message)
            .where(Message.session_id == sid)
            .order_by(Message.created_at.asc())
        )
        messages = r.scalars().all()
    return [MessageItem(role=m.role, content=m.content or "") for m in messages]


class UpdateSessionRequest(BaseModel):
    title: str | None = None


@app.patch("/sessions/{session_id}")
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


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict:
    """Delete a conversation (and its messages/summaries). Cleanup / 清理."""
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


# --- Chat ---
class ChatMessage(BaseModel):
    role: str
    content: str


# 深度思考（Deep Thinking）系统提示：要求模型分步骤推理、逻辑严谨
DEEP_THINKING_SYSTEM = (
    "你是一位资深专家，需要对问题进行深度思考。请严格按以下步骤输出：\n"
    "1. 分析问题背景和核心矛盾\n"
    "2. 拆解关键影响因素（至少3点）\n"
    "3. 逐步推理可能的解决方案\n"
    "4. 评估每个方案的优缺点\n"
    "5. 给出最终结论和建议\n"
    "要求：逻辑严谨、细节充分、避免笼统表述。"
)

# 深度研究（Deep Research）系统提示：资深研究员、多步推理与证据支持（参考 .local/如何深度研究.md）
DEEP_RESEARCH_SYSTEM = (
    "你是一位资深研究员，需要对问题进行深度研究。请严格按以下步骤输出：\n"
    "1. 分析问题背景和核心矛盾\n"
    "2. 拆解关键影响因素（至少3点）\n"
    "3. 逐步推理可能的解决方案\n"
    "4. 评估每个方案的优缺点\n"
    "5. 给出最终结论和建议\n"
    "要求：逻辑严谨、细节充分、避免笼统表述。"
)


class ChatRequest(BaseModel):
    session_id: str
    messages: list[ChatMessage]
    model: str | None = None
    stream: bool = True
    deep_thinking: bool = False
    deep_research: bool = False


@app.post("/chat")
async def chat(req: ChatRequest):
    """Stream chat completion; optionally store messages and run embedding. deep_thinking / deep_research 开启时使用对应系统提示与低 temperature、大 max_tokens."""
    config = get_config()
    chat_providers = getattr(config, "chat_providers", {}) or {}
    default_chat = config.default_chat_provider or "dashscope"
    if default_chat not in chat_providers:
        raise HTTPException(status_code=503, detail="no chat provider configured")
    prov = chat_providers[default_chat]
    adapter = CloudAPIAdapter(
        api_key_env=prov.api_key_env,
        endpoint=prov.endpoint,
        model=req.model or prov.model,
    )
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    prompt = req.messages[-1].content if req.messages else ""
    extra = {}
    if req.deep_research:
        messages = [{"role": "system", "content": DEEP_RESEARCH_SYSTEM}] + messages
        extra = {"temperature": 0.2, "max_tokens": 3000, "top_p": 0.6}
    elif req.deep_thinking:
        messages = [{"role": "system", "content": DEEP_THINKING_SYSTEM}] + messages
        extra = {"temperature": 0.2, "max_tokens": 2500, "top_p": 0.6}

    def _chat_error_detail(exc: httpx.HTTPStatusError) -> str:
        msg = f"Chat API error: {exc.response.status_code} {exc.response.reason_phrase}"
        if exc.response.status_code == 401:
            msg += ". Set a valid API key in config/app.yaml (dashscope_api_key) or DASHSCOPE_API_KEY."
        return msg

    if not req.stream:
        try:
            t0 = time.perf_counter()
            text, usage = await adapter.call(prompt, messages=messages, **extra)
            duration_ms = round((time.perf_counter() - t0) * 1000, 1)
            await _persist_chat_messages(req.session_id, prompt, text)
            return {
                "choices": [{"message": {"role": "assistant", "content": text}}],
                "usage": usage,
                "duration_ms": duration_ms,
            }
        except httpx.HTTPStatusError as e:
            logger.warning("chat_api_error", status=e.response.status_code, detail=str(e))
            raise HTTPException(status_code=503, detail=_chat_error_detail(e)) from e

    async def stream():
        full_content = ""
        usage: dict = {}
        t0 = time.perf_counter()
        try:
            async for chunk in adapter.stream_call(prompt, messages=messages, **extra):
                if isinstance(chunk, dict) and "_usage" in chunk:
                    usage = chunk["_usage"]
                    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
                    yield f"data: {json.dumps({'usage': usage, 'duration_ms': duration_ms})}\n\n"
                    continue
                full_content += chunk
                yield f"data: {json.dumps({'choices': [{'delta': {'content': chunk}}]})}\n\n"
        except httpx.HTTPStatusError as e:
            logger.warning("chat_api_error", status=e.response.status_code, detail=str(e))
            err_msg = _chat_error_detail(e)
            yield f"data: {json.dumps({'choices': [{'delta': {'content': err_msg}}]})}\n\n"
            full_content = ""
        if full_content:
            await _persist_chat_messages(req.session_id, prompt, full_content)
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        yield f"data: {json.dumps({'usage': usage, 'duration_ms': duration_ms})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


# --- Search (semantic) ---
@app.get("/sessions/{session_id}/search")
async def session_search(session_id: str, query: str, limit: int = 5) -> dict:
    """Semantic search over session messages (embedding similarity)."""
    vec = await get_embedding(query)
    if not vec:
        return {"matches": []}
    vec_str = "[" + ",".join(str(x) for x in vec) + "]"
    factory = get_session_factory()
    async with factory() as session:
        # Simple similarity search if pgvector is available
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


# --- Static web UI ---
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/")
async def index():
    """Serve the chat web UI."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Static files not found")
    return FileResponse(index_file)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
