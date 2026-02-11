"""
Code review runner: gather code files under path, build prompt, invoke Copilot or Claude CLI.

- Path is restricted to a root directory (default: current working directory).
- Supports provider "copilot" or "claude"; both accept -p / --prompt for non-interactive run.
- Long prompts are written to a temp file to avoid argv length limits.
- Optional git mode: pass a list of commits; checks commits are in current tree and working tree
  is clean, then reviews only the changes in those commits (git show).
"""

import os
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any, Generator

# Common code extensions to include
DEFAULT_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".swift", ".kt", ".md", ".yaml", ".yml", ".json"}
# Max total bytes of file content to send
DEFAULT_MAX_BYTES = 300_000
# Max number of files
DEFAULT_MAX_FILES = 200

REVIEW_PROMPT_TEMPLATE = """Perform a structured code review of the following codebase. Output a report in the following format:

## Summary
Brief overall assessment (2-3 sentences).

## Files Reviewed
List the main files or modules covered.

## Issues
- List potential bugs, security issues, or anti-patterns with file/line references if possible.
- Use bullet points.

## Suggestions
- Improvement suggestions (readability, performance, structure, tests).
- Use bullet points.

## Positive Notes
- What is done well.

---

Code to review:

{code_block}
"""


def _resolve_root(root: str | Path | None) -> Path:
    """Return resolved absolute root path; default to cwd."""
    if root is None or root == "":
        return Path.cwd().resolve()
    return Path(root).resolve()


def _is_safe_path(resolved: Path, root: Path) -> bool:
    """Ensure path is under root (no escape)."""
    try:
        resolved.relative_to(root)
        return True
    except ValueError:
        return False


def _git_root(start: Path) -> Path:
    """Return git repository root containing start; raise ValueError if not a git repo."""
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if r.returncode != 0 or not r.stdout:
        raise ValueError("not a git repository")
    return Path(r.stdout.strip()).resolve()


def _check_commits_in_tree(commits: list[str], root: Path) -> None:
    """Raise ValueError if any commit is not in the current git tree (not an ancestor of HEAD)."""
    for c in commits:
        c = c.strip()
        if not c:
            continue
        r = subprocess.run(
            ["git", "merge-base", "--is-ancestor", c, "HEAD"],
            cwd=root,
            capture_output=True,
            timeout=5,
        )
        if r.returncode != 0:
            raise ValueError(f"commit {c!r} is not in the current git tree (not an ancestor of HEAD)")


def _check_git_clean(root: Path) -> None:
    """Raise ValueError if working tree has uncommitted changes (not clean)."""
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if r.returncode != 0:
        raise ValueError("git status failed")
    if (r.stdout or "").strip():
        raise ValueError("working tree is not clean (uncommitted or untracked changes)")


def validate_commits_for_review(
    commits: list[str],
    root: str | Path | None = None,
) -> tuple[bool, str | None]:
    """
    Check that commits are in current tree and working tree is clean.
    Returns (True, None) if valid, else (False, error_message).
    """
    commit_list = [c.strip() for c in commits if c and c.strip()]
    if not commit_list:
        return (False, "请填写至少一个 commit")
    resolved_root = _resolve_root(root)
    try:
        git_root = _git_root(resolved_root)
        _check_commits_in_tree(commit_list, git_root)
        _check_git_clean(git_root)
    except ValueError as e:
        return (False, str(e))
    return (True, None)


def gather_diffs_from_commits(
    commits: list[str],
    root: Path,
    max_total_bytes: int = DEFAULT_MAX_BYTES,
) -> list[tuple[str, str]]:
    """
    Return [(label, patch), ...] for each given commit (git show --no-color).
    commits must already be validated (in tree, clean checked by caller).
    """
    out: list[tuple[str, str]] = []
    total_bytes = 0
    for c in commits:
        c = c.strip()
        if not c:
            continue
        r = subprocess.run(
            ["git", "show", c, "--no-color", "--format="],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            raise ValueError(f"git show {c!r} failed: {r.stderr or r.stdout or 'unknown'}")
        patch = (r.stdout or "").strip()
        size = len(patch.encode("utf-8"))
        if total_bytes + size > max_total_bytes:
            patch = patch[: max(0, max_total_bytes - total_bytes)] + "\n... (truncated)"
            total_bytes = max_total_bytes
        else:
            total_bytes += size
        out.append((f"commit {c}", patch))
    return out


def gather_diffs_from_uncommitted(
    root: Path,
    path: str | None = None,
    max_total_bytes: int = DEFAULT_MAX_BYTES,
) -> list[tuple[str, str]]:
    """
    Return [(label, patch)] for current uncommitted changes (staged + unstaged).
    Uses `git diff HEAD --no-color`; if path is given (e.g. "app"), only changes under that path.
    Returns empty list if no changes.
    """
    cmd: list[str] = ["git", "diff", "HEAD", "--no-color"]
    if path and path.strip():
        target = (root / path.strip()).resolve()
        if _is_safe_path(target, root):
            cmd.extend(["--", path.strip()])
    r = subprocess.run(
        cmd,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        raise ValueError(f"git diff HEAD failed: {r.stderr or r.stdout or 'unknown'}")
    patch = (r.stdout or "").strip()
    if not patch:
        return []
    size = len(patch.encode("utf-8"))
    if size > max_total_bytes:
        patch = patch[:max_total_bytes] + "\n... (truncated)"
    label = "current changes (uncommitted)" + (f" under {path.strip()}" if path and path.strip() else "")
    return [(label, patch)]


REVIEW_PROMPT_TEMPLATE_DIFF = """Review the following git commit change(s). Output a report in the same format as a code review:

## Summary
Brief overall assessment of the changes (2-3 sentences).

## Files Reviewed
List the main files or modules touched by these commits.

## Issues
- Potential bugs, security issues, or anti-patterns with file/line references if possible.
- Use bullet points.

## Suggestions
- Improvement suggestions (readability, performance, structure, tests).
- Use bullet points.

## Positive Notes
- What is done well.

---

Git patches to review:

{code_block}
"""


def gather_code_files(
    path: str | Path,
    root: str | Path | None = None,
    extensions: set[str] | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_total_bytes: int = DEFAULT_MAX_BYTES,
) -> list[tuple[Path, str]]:
    """
    Collect (relative_path, content) for code files under path.
    path is relative to root; root defaults to cwd.
    """
    root = _resolve_root(root)
    target = (root / path).resolve() if path else root
    if not _is_safe_path(target, root):
        raise ValueError(f"path is outside allowed root: {path!r}")
    if not target.exists():
        raise ValueError(f"path does not exist: {path!r}")
    ext = extensions or DEFAULT_EXTENSIONS
    out: list[tuple[Path, str]] = []
    total_bytes = 0
    if target.is_file():
        if target.suffix.lower() in ext or not ext:
            try:
                content = target.read_text(encoding="utf-8", errors="replace")
                out.append((target.relative_to(root), content))
            except Exception:
                pass
        return out
    for f in target.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lower() not in ext:
            continue
        if len(out) >= max_files:
            break
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            rel = f.relative_to(root)
            size = len(content.encode("utf-8"))
            if total_bytes + size > max_total_bytes:
                content = content[: max(0, max_total_bytes - total_bytes)] + "\n... (truncated)"
                total_bytes = max_total_bytes
            else:
                total_bytes += size
            out.append((rel, content))
        except Exception:
            continue
    return out


def build_review_prompt(files: list[tuple[Path, str]]) -> str:
    """Build a single prompt string with all file contents."""
    parts = []
    for rel, content in files:
        parts.append(f"### File: {rel}\n```\n{content}\n```")
    code_block = "\n\n".join(parts)
    return REVIEW_PROMPT_TEMPLATE.format(code_block=code_block)


def build_review_prompt_from_diffs(diffs: list[tuple[str, str]]) -> str:
    """Build prompt string from git diff (label, patch) list."""
    parts = []
    for label, patch in diffs:
        parts.append(f"### {label}\n```diff\n{patch}\n```")
    code_block = "\n\n".join(parts)
    return REVIEW_PROMPT_TEMPLATE_DIFF.format(code_block=code_block)


def run_code_review(
    path: str | Path,
    provider: str,
    root: str | Path | None = None,
    commits: list[str] | None = None,
    uncommitted_only: bool = False,
    max_files: int = DEFAULT_MAX_FILES,
    max_total_bytes: int = DEFAULT_MAX_BYTES,
    timeout_seconds: int = 180,
) -> dict[str, str | int]:
    """
    Run code review via Copilot or Claude CLI.

    If uncommitted_only: review only current uncommitted changes (git diff HEAD).
    If commits is non-empty: validate in-tree + clean, then review only those commit diffs.
    Otherwise: review code under path.
    Returns:
        {"report": str, "provider": str, "files_included": int, "stderr": str}
    """
    provider = (provider or "").strip().lower()
    if provider not in ("copilot", "claude"):
        raise ValueError(f"provider must be 'copilot' or 'claude', got: {provider!r}")

    resolved_root = _resolve_root(root)
    if uncommitted_only:
        git_root = _git_root(resolved_root)
        diffs = gather_diffs_from_uncommitted(git_root, path=str(path) if path else None, max_total_bytes=max_total_bytes)
        if not diffs:
            return {
                "report": "No uncommitted changes to review.",
                "provider": provider,
                "files_included": 0,
                "stderr": "",
            }
        prompt = build_review_prompt_from_diffs(diffs)
        files_count = len(diffs)
    elif commits:
        commit_list = [c.strip() for c in commits if c and c.strip()]
        if not commit_list:
            raise ValueError("commits list is empty")
        git_root = _git_root(resolved_root)
        _check_commits_in_tree(commit_list, git_root)
        _check_git_clean(git_root)
        diffs = gather_diffs_from_commits(commit_list, git_root, max_total_bytes=max_total_bytes)
        if not diffs:
            return {
                "report": "No diff content from the given commits.",
                "provider": provider,
                "files_included": 0,
                "stderr": "",
            }
        prompt = build_review_prompt_from_diffs(diffs)
        files_count = len(diffs)
    else:
        files = gather_code_files(path, root=root, max_files=max_files, max_total_bytes=max_total_bytes)
        if not files:
            return {
                "report": "No code files found under the given path.",
                "provider": provider,
                "files_included": 0,
                "stderr": "",
            }
        prompt = build_review_prompt(files)
        files_count = len(files)
    # CLI argv limit: keep prompt under ~28k chars
    max_chars = 28_000
    if len(prompt) > max_chars:
        prompt = prompt[:max_chars] + "\n\n... (content truncated for CLI limit)"

    cmd: list[str] = ["claude", "-p", prompt] if provider == "claude" else ["copilot", "--prompt", prompt]
    env = {**os.environ, "COPILOT_ALLOW_ALL": "1"} if provider == "copilot" else os.environ

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=env,
    )
    report = (result.stdout or "").strip()
    if not report and result.stderr:
        report = f"(No stdout; stderr:\n{result.stderr})"
    return {
        "report": report,
        "provider": provider,
        "files_included": files_count,
        "stderr": result.stderr or "",
    }


def run_code_review_stream(
    path: str | Path,
    provider: str,
    root: str | Path | None = None,
    commits: list[str] | None = None,
    uncommitted_only: bool = False,
    max_files: int = DEFAULT_MAX_FILES,
    max_total_bytes: int = DEFAULT_MAX_BYTES,
    timeout_seconds: int = 180,
) -> Generator[dict[str, Any], None, None]:
    """
    Run code review and yield stream events: {"type": "log", "message": "..."} then {"type": "report", ...}.
    If uncommitted_only: review only current uncommitted changes.
    If commits is non-empty: validate in-tree + clean, then review only those commit diffs.
    """
    provider = (provider or "").strip().lower()
    if provider not in ("copilot", "claude"):
        raise ValueError(f"provider must be 'copilot' or 'claude', got: {provider!r}")

    resolved_root = _resolve_root(root)
    if uncommitted_only:
        path_msg = f" under {path}" if path and str(path).strip() else ""
        yield {"type": "log", "message": "正在收集当前未提交的变更（git diff HEAD" + path_msg + "）…"}
        git_root = _git_root(resolved_root)
        diffs = gather_diffs_from_uncommitted(git_root, path=str(path) if path else None, max_total_bytes=max_total_bytes)
        if not diffs:
            yield {"type": "report", "report": "No uncommitted changes to review.", "provider": provider, "files_included": 0, "stderr": ""}
            return
        yield {"type": "log", "message": "已收集当前变更，正在构建审查提示…"}
        prompt = build_review_prompt_from_diffs(diffs)
        files_count = len(diffs)
    elif commits:
        commit_list = [c.strip() for c in commits if c and c.strip()]
        if not commit_list:
            raise ValueError("commits list is empty")
        yield {"type": "log", "message": "正在检查 Git：提交是否在当前树、工作区是否 clean…"}
        git_root = _git_root(resolved_root)
        _check_commits_in_tree(commit_list, git_root)
        _check_git_clean(git_root)
        yield {"type": "log", "message": "Git 检查通过，正在收集指定提交的变更…"}
        diffs = gather_diffs_from_commits(commit_list, git_root, max_total_bytes=max_total_bytes)
        if not diffs:
            yield {"type": "report", "report": "No diff content from the given commits.", "provider": provider, "files_included": 0, "stderr": ""}
            return
        yield {"type": "log", "message": f"已收集 {len(diffs)} 个提交的 diff。"}
        yield {"type": "log", "message": "正在构建审查提示…"}
        prompt = build_review_prompt_from_diffs(diffs)
        files_count = len(diffs)
    else:
        yield {"type": "log", "message": "正在收集代码文件…"}
        files = gather_code_files(path, root=root, max_files=max_files, max_total_bytes=max_total_bytes)
        if not files:
            yield {"type": "report", "report": "No code files found under the given path.", "provider": provider, "files_included": 0, "stderr": ""}
            return

        yield {"type": "log", "message": f"已找到 {len(files)} 个文件。"}
        yield {"type": "log", "message": "正在构建审查提示…"}
        prompt = build_review_prompt(files)
        files_count = len(files)
    max_chars = 28_000
    if len(prompt) > max_chars:
        prompt = prompt[:max_chars] + "\n\n... (content truncated for CLI limit)"
    yield {"type": "log", "message": f"正在启动 {provider} …"}
    cmd: list[str] = ["claude", "-p", prompt] if provider == "claude" else ["copilot", "--prompt", prompt]
    env = {**os.environ, "COPILOT_ALLOW_ALL": "1"} if provider == "copilot" else os.environ

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        bufsize=1,
    )
    out_q: queue.Queue[str | None] = queue.Queue()
    err_q: queue.Queue[str | None] = queue.Queue()

    def read_stdout():
        if proc.stdout:
            for line in iter(proc.stdout.readline, ""):
                out_q.put(line)
        out_q.put(None)

    def read_stderr():
        if proc.stderr:
            for line in iter(proc.stderr.readline, ""):
                err_q.put("[stderr] " + line.rstrip() + "\n")
        err_q.put(None)

    t_out = threading.Thread(target=read_stdout, daemon=True)
    t_err = threading.Thread(target=read_stderr, daemon=True)
    t_out.start()
    t_err.start()

    report_parts: list[str] = []
    out_done = False
    err_done = False
    while not (out_done and err_done):
        try:
            line = out_q.get(timeout=0.2)
            if line is None:
                out_done = True
            else:
                report_parts.append(line)
                yield {"type": "log", "message": line.rstrip()}
        except queue.Empty:
            pass
        try:
            line = err_q.get(timeout=0.2)
            if line is None:
                err_done = True
            else:
                yield {"type": "log", "message": line.rstrip()}
        except queue.Empty:
            pass

    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    report = "".join(report_parts).strip()
    yield {"type": "report", "report": report, "provider": provider, "files_included": files_count, "stderr": ""}
