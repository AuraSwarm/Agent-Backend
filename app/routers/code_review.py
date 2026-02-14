"""Code review (run, validate, stream) and code review history CRUD."""

import asyncio
import json
import os
import queue
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.code_review.runner import run_code_review, run_code_review_stream, validate_commits_for_review
from app.storage.db import get_session_factory, log_audit, session_scope
from app.storage.models import CodeReview

router = APIRouter(tags=["code_review"])


def _code_review_root() -> str | None:
    return os.environ.get("CODE_REVIEW_ROOT") or None


def _repo_address(root: str | None) -> str:
    """Main title: repo address (git remote origin URL or resolved root path)."""
    resolved = Path(root).resolve() if root else Path.cwd().resolve()
    r = subprocess.run(
        ["git", "-C", str(resolved), "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if r.returncode == 0 and (r.stdout or "").strip():
        url = r.stdout.strip()
        if url.endswith(".git"):
            url = url[:-4]
        if url.startswith("git@"):
            url = url.replace(":", "/", 1).replace("git@", "https://", 1)
        return url
    return str(resolved)


def _code_review_subtitle(mode: str, path: str | None, commits: list[str] | None, uncommitted_only: bool) -> str:
    """Subtitle: review mode description."""
    if mode == "uncommitted" or uncommitted_only:
        return "当前变更"
    if mode == "git" and commits:
        return "Git " + ", ".join((c[:8] for c in commits[:3])) + ("…" if len(commits) > 3 else "")
    return "按路径 " + (path or "app")


def _code_review_title(
    mode: str, path: str | None, commits: list[str] | None, uncommitted_only: bool, root: str | None = None
) -> str:
    """Title = main (repo address) + subtitle (review mode)."""
    main = _repo_address(root)
    sub = _code_review_subtitle(mode, path, commits, uncommitted_only)
    return f"{main} — {sub}"


# --- Schemas ---
class CodeReviewRequest(BaseModel):
    path: str = Field("app", description="Relative path from root")
    provider: str = Field("claude", description="'copilot' or 'claude'")
    commits: list[str] | None = None
    uncommitted_only: bool = False
    max_files: int = Field(200, ge=1, le=500)
    max_total_bytes: int = Field(300_000, ge=1000, le=1_000_000)
    timeout_seconds: int = Field(180, ge=30, le=600)


class ValidateCommitsRequest(BaseModel):
    commits: list[str] = Field(..., description="Commit list to validate")


class CodeReviewListItem(BaseModel):
    id: str
    created_at: str
    title: str | None
    mode: str
    provider: str
    files_included: int


class CodeReviewDetail(BaseModel):
    id: str
    created_at: str
    mode: str
    path: str | None
    commits: list[str] | None
    uncommitted_only: bool
    provider: str
    report: str
    files_included: int
    title: str | None


class CodeReviewCreate(BaseModel):
    mode: str = Field(..., description="path, git, or uncommitted")
    path: str | None = None
    commits: list[str] | None = None
    uncommitted_only: bool = False
    provider: str = Field(...)
    report: str = Field(...)
    files_included: int = 0


def _code_review_stream_gen(path: str, provider: str, root: str | None, commits: list[str] | None,
                            uncommitted_only: bool, max_files: int, max_total_bytes: int,
                            timeout_seconds: int, log_queue: queue.Queue[dict | None]) -> None:
    try:
        for event in run_code_review_stream(
            path, provider, root=root, commits=commits, uncommitted_only=uncommitted_only,
            max_files=max_files, max_total_bytes=max_total_bytes, timeout_seconds=timeout_seconds,
        ):
            log_queue.put(event)
    except Exception as e:
        log_queue.put({"type": "error", "message": str(e)})
    finally:
        log_queue.put(None)


# --- Routes ---
@router.post("/code-review/validate-commits")
async def code_review_validate_commits(req: ValidateCommitsRequest) -> dict:
    """Validate commits are in current tree and working tree is clean."""
    root = _code_review_root()
    valid, error = await asyncio.to_thread(validate_commits_for_review, req.commits, root=root)
    if valid:
        return {"valid": True}
    return {"valid": False, "error": error or "validation failed"}


@router.post("/code-review")
async def code_review(req: CodeReviewRequest) -> dict:
    """Run code review on path or git commits or uncommitted; return report."""
    root = _code_review_root()
    try:
        result = await asyncio.to_thread(
            run_code_review,
            req.path,
            req.provider,
            root=root,
            commits=req.commits,
            uncommitted_only=req.uncommitted_only,
            max_files=req.max_files,
            max_total_bytes=req.max_total_bytes,
            timeout_seconds=req.timeout_seconds,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="code review timeout") from None


@router.post("/code-review/stream")
async def code_review_stream(req: CodeReviewRequest):
    """Stream code review progress and report as SSE."""
    root = _code_review_root()
    log_queue: queue.Queue[dict | None] = queue.Queue()

    def start_thread():
        _code_review_stream_gen(
            req.path, req.provider, root, req.commits, req.uncommitted_only,
            req.max_files, req.max_total_bytes, req.timeout_seconds, log_queue,
        )

    asyncio.get_event_loop().run_in_executor(None, start_thread)

    async def stream():
        while True:
            event = await asyncio.to_thread(log_queue.get)
            if event is None:
                break
            if event.get("type") == "error":
                yield f"data: {json.dumps(event)}\n\n"
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/code-reviews", response_model=list[CodeReviewListItem])
async def list_code_reviews(limit: int = 50) -> list[CodeReviewListItem]:
    """List recent code reviews for sidebar."""
    limit = min(limit, 100)
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(
            select(CodeReview).order_by(CodeReview.created_at.desc()).limit(limit)
        )
        reviews = r.scalars().all()
    return [
        CodeReviewListItem(
            id=str(rev.id),
            created_at=rev.created_at.isoformat() if rev.created_at else "",
            title=rev.title or _code_review_title(rev.mode, rev.path, rev.commits, rev.uncommitted_only, _code_review_root()),
            mode=rev.mode,
            provider=rev.provider,
            files_included=rev.files_included or 0,
        )
        for rev in reviews
    ]


@router.get("/code-reviews/{review_id}", response_model=CodeReviewDetail)
async def get_code_review(review_id: str) -> CodeReviewDetail:
    try:
        rid = uuid.UUID(review_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="review not found")
    factory = get_session_factory()
    async with factory() as db:
        r = await db.execute(select(CodeReview).where(CodeReview.id == rid))
        rev = r.scalar_one_or_none()
    if not rev:
        raise HTTPException(status_code=404, detail="review not found")
    return CodeReviewDetail(
        id=str(rev.id),
        created_at=rev.created_at.isoformat() if rev.created_at else "",
        mode=rev.mode,
        path=rev.path,
        commits=rev.commits,
        uncommitted_only=rev.uncommitted_only or False,
        provider=rev.provider,
        report=rev.report or "",
        files_included=rev.files_included or 0,
        title=rev.title,
    )


@router.post("/code-reviews", response_model=CodeReviewDetail)
async def create_code_review(body: CodeReviewCreate) -> CodeReviewDetail:
    title = _code_review_title(body.mode, body.path, body.commits, body.uncommitted_only, _code_review_root())
    async with session_scope() as db:
        rev = CodeReview(
            id=uuid.uuid4(),
            mode=body.mode,
            path=body.path,
            commits=body.commits,
            uncommitted_only=body.uncommitted_only,
            provider=body.provider,
            report=body.report,
            files_included=body.files_included,
            title=title,
        )
        db.add(rev)
        await db.flush()
        await log_audit(db, "create_code_review", "code_review", resource_id=str(rev.id))
    return CodeReviewDetail(
        id=str(rev.id),
        created_at=rev.created_at.isoformat() if rev.created_at else "",
        mode=rev.mode,
        path=rev.path,
        commits=rev.commits,
        uncommitted_only=rev.uncommitted_only,
        provider=rev.provider,
        report=rev.report,
        files_included=rev.files_included,
        title=rev.title,
    )


class CodeReviewUpdateTitle(BaseModel):
    title: str | None = None


@router.patch("/code-reviews/{review_id}")
async def update_code_review_title(review_id: str, body: CodeReviewUpdateTitle) -> dict:
    """Update code review title (rename)."""
    if body.title is None:
        return {"status": "ok"}
    try:
        rid = uuid.UUID(review_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="review not found")
    async with session_scope() as db:
        r = await db.execute(select(CodeReview).where(CodeReview.id == rid))
        rev = r.scalar_one_or_none()
        if not rev:
            raise HTTPException(status_code=404, detail="review not found")
        rev.title = body.title.strip() or None
        await db.commit()
    return {"status": "ok"}


@router.delete("/code-reviews/{review_id}")
async def delete_code_review(review_id: str) -> dict:
    try:
        rid = uuid.UUID(review_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="review not found")
    async with session_scope() as db:
        r = await db.execute(select(CodeReview).where(CodeReview.id == rid))
        rev = r.scalar_one_or_none()
        if not rev:
            raise HTTPException(status_code=404, detail="review not found")
        await db.execute(delete(CodeReview).where(CodeReview.id == rid))
        await log_audit(db, "delete_code_review", "code_review", resource_id=review_id)
    return {"status": "ok", "message": "review deleted"}
