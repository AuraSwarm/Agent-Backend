"""Chat completion (stream and non-stream) with optional deep thinking / deep research."""

import json
import time
import uuid

import httpx
import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy import insert

from app.config.loader import get_config
from app.storage.db import get_session_factory, session_scope
from app.storage.long_term import get_long_term_backend, is_long_term_oss
from app.storage.models import Message, Session

router = APIRouter(tags=["chat"])

logger = structlog.get_logger(__name__)


def _is_task_session(s) -> bool:
    """True if session is a task (metadata.is_task). 对话与任务分离，POST /chat 仅限对话。"""
    return bool((getattr(s, "metadata_", None) or {}).get("is_task"))

DEEP_THINKING_SYSTEM = (
    "你是一位资深专家，需要对问题进行深度思考。请严格按以下步骤输出：\n"
    "1. 分析问题背景和核心矛盾\n"
    "2. 拆解关键影响因素（至少3点）\n"
    "3. 逐步推理可能的解决方案\n"
    "4. 评估每个方案的优缺点\n"
    "5. 给出最终结论和建议\n"
    "要求：逻辑严谨、细节充分、避免笼统表述。"
)

DEEP_RESEARCH_SYSTEM = (
    "你是一位资深研究员，需要对问题进行深度研究。请严格按以下步骤输出：\n"
    "1. 分析问题背景和核心矛盾\n"
    "2. 拆解关键影响因素（至少3点）\n"
    "3. 逐步推理可能的解决方案\n"
    "4. 评估每个方案的优缺点\n"
    "5. 给出最终结论和建议\n"
    "要求：逻辑严谨、细节充分、避免笼统表述。"
)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    session_id: str
    messages: list[ChatMessage]
    user_id: str | None = None  # Optional; when set and long-term OSS enabled, profile/knowledge are injected
    model: str | None = None
    stream: bool = True
    deep_thinking: bool = False
    deep_research: bool = False


async def _persist_chat_messages(
    session_id: str,
    user_content: str,
    assistant_content: str,
    model: str | None = None,
) -> None:
    """Save user and assistant messages to DB. model 为回复使用的对话模型 ID，用于前端展示。"""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        logger.warning(
            "persist_chat_messages_invalid_session_id",
            session_id=session_id,
            hint="session_id must be a valid UUID",
        )
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
                model=model,
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


def _chat_error_detail(exc: httpx.HTTPStatusError) -> str:
    msg = f"Chat API error: {exc.response.status_code} {exc.response.reason_phrase}"
    if exc.response.status_code == 401:
        msg += ". Set a valid API key in config/app.yaml (dashscope_api_key) or DASHSCOPE_API_KEY."
    return msg


def _build_long_term_system_prefix(user_id: str, last_user_content: str) -> str:
    """Load profile and relevant knowledge from long-term storage; return system message prefix."""
    from memory_base import load_user_profile, retrieve_relevant_knowledge

    backend = get_long_term_backend()
    parts = []
    profile = load_user_profile(backend, user_id)
    if profile:
        traits = profile.get("traits") or {}
        if traits:
            parts.append("用户画像: " + ", ".join(f"{k}={v}" for k, v in traits.items() if v))
    prompt_for_knowledge = (last_user_content or "").strip()[:200]
    if prompt_for_knowledge:
        triples = retrieve_relevant_knowledge(backend, user_id, prompt_for_knowledge, top_k=5)
        if triples:
            parts.append("相关知识: " + "; ".join(f"({s},{p},{o})" for s, p, o in triples))
    if not parts:
        return ""
    return "长期记忆:\n" + "\n".join(parts) + "\n\n"


@router.post("/chat")
async def chat(req: ChatRequest):
    """Stream or non-stream chat completion（仅限对话）。任务反馈请用 POST /api/chat/room/{id}/message。"""
    try:
        sid = uuid.UUID(req.session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="session not found")
    async with session_scope() as db:
        r = await db.execute(select(Session).where(Session.id == sid))
        s = r.scalar_one_or_none()
        if not s:
            raise HTTPException(status_code=404, detail="session not found")
        if _is_task_session(s):
            raise HTTPException(status_code=404, detail="use POST /api/chat/room/{id}/message for tasks")
    from app.adapters.factory import build_chat_adapter
    from app.routers.team_room import _resolve_provider_for_model

    config = get_config()
    chat_providers = getattr(config, "chat_providers", {}) or {}
    default_chat = config.default_chat_provider or "dashscope"
    if default_chat not in chat_providers:
        raise HTTPException(status_code=503, detail="no chat provider configured")
    default_prov = chat_providers[default_chat]
    model = req.model or getattr(default_prov, "model", None)
    if not model:
        models_list = getattr(default_prov, "models", None) or []
        model = models_list[0] if models_list else None
    if not model:
        raise HTTPException(status_code=503, detail="no chat model configured")
    prov_name, prov = _resolve_provider_for_model(config, model)
    if prov_name is None:
        prov_name, prov = default_chat, default_prov
    adapter = build_chat_adapter(prov, model)
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    prompt = req.messages[-1].content if req.messages else ""
    # Inject long-term memory (profile + knowledge) when using OSS
    if is_long_term_oss():
        effective_user_id = (req.user_id or req.session_id).strip()
        if effective_user_id:
            last_content = req.messages[-1].content if req.messages else ""
            prefix = _build_long_term_system_prefix(effective_user_id, last_content)
            if prefix:
                messages = [{"role": "system", "content": prefix}] + messages
    extra = {}
    if req.deep_research:
        messages = [{"role": "system", "content": DEEP_RESEARCH_SYSTEM}] + messages
        extra = {"temperature": 0.2, "max_tokens": 3000, "top_p": 0.6}
    elif req.deep_thinking:
        messages = [{"role": "system", "content": DEEP_THINKING_SYSTEM}] + messages
        extra = {"temperature": 0.2, "max_tokens": 2500, "top_p": 0.6}

    if not req.stream:
        try:
            t0 = time.perf_counter()
            text, usage = await adapter.call(prompt, messages=messages, **extra)
            duration_ms = round((time.perf_counter() - t0) * 1000, 1)
            await _persist_chat_messages(req.session_id, prompt, text, model=model)
            return {
                "choices": [{"message": {"role": "assistant", "content": text}}],
                "usage": usage,
                "duration_ms": duration_ms,
            }
        except ValueError as e:
            logger.warning("chat_config_error", error=str(e))
            raise HTTPException(status_code=503, detail=str(e)) from e
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
        except ValueError as e:
            logger.warning("chat_config_error", error=str(e))
            yield f"data: {json.dumps({'choices': [{'delta': {'content': str(e)}}]})}\n\n"
            full_content = ""
        except httpx.HTTPStatusError as e:
            logger.warning("chat_api_error", status=e.response.status_code, detail=str(e))
            err_msg = _chat_error_detail(e)
            yield f"data: {json.dumps({'choices': [{'delta': {'content': err_msg}}]})}\n\n"
            full_content = ""
        except Exception as e:
            logger.warning("chat_stream_error", error=str(e))
            err_msg = str(e) or "回复失败"
            yield f"data: {json.dumps({'choices': [{'delta': {'content': err_msg}}]})}\n\n"
            full_content = ""
        if full_content:
            await _persist_chat_messages(req.session_id, prompt, full_content, model=model)
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        yield f"data: {json.dumps({'usage': usage, 'duration_ms': duration_ms})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
