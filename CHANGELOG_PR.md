# Change note – PR to `main`

**Branch:** `sandbox/jy/init_cmt` → `main`

---

## Summary

This PR adds the Agent Backend service (FastAPI, config-driven chat, embeddings, sessions, Web UI) and extends the Chat API with **deep thinking**, **deep research**, stream **usage/duration** stats, and a Web UI **stop** button. It also adds **Code Review** (path / git commits / uncommitted changes) with streaming, validation, persisted history, and a **Code** tab in the Web UI. Documentation and tests are included.

---

## Features

### Chat API

- **POST /chat** – Streaming and non-streaming completions; session-based.
- **Optional parameters:**
  - `deep_thinking` (bool): Enables “深度思考” system prompt (step-by-step reasoning, `temperature=0.2`, `max_tokens=2500`).
  - `deep_research` (bool): Enables “深度研究” system prompt (researcher-style analysis, `max_tokens=3000`). If both are `true`, deep research is used.
- **Response:**
  - Non-stream: `choices`, `usage` (prompt/completion/total tokens), `duration_ms`.
  - Stream: SSE with content deltas; final event `{"usage": {...}, "duration_ms": ...}`; client may abort to stop the stream.

### Sessions & cleanup

- **GET /sessions** – List sessions with `first_message_preview`, `last_message_preview`, `topic_summary`.
- **GET /sessions/{id}/messages** – Messages for a session.
- **DELETE /sessions/{id}** – Delete a session (cleanup).

### Code Review

- **POST /code-review** – Run code review (path or git commits or uncommitted); returns report.
- **POST /code-review/stream** – Same as above with SSE (log lines + final report).
- **POST /code-review/validate-commits** – Validate that given commits are in current git tree and working tree is clean; returns `{ "valid": true }` or `{ "valid": false, "error": "..." }`. Used by Web UI to block run when invalid.
- **Review modes:** (1) **Full repo** – review all code under a path; (2) **By Git commit** – commit list required, validated (in tree + clean) before run; (3) **Current changes** – `git diff HEAD` (optional path scope).
- **Path** is required for all modes (path, git, uncommitted) in the Web UI.
- **Code review history:** **GET /code-reviews** (list), **GET /code-reviews/{id}** (detail), **POST /code-reviews** (save after run), **DELETE /code-reviews/{id}** (delete). Stored in DB table `code_reviews`. Saved **title** = repo address (main) + review mode (subtitle), e.g. `https://github.com/org/repo — 按路径 app`; repo is resolved from `git config remote.origin.url` when available, else root path.

### Web UI

- **Tabs:** Chat | Code.
- **Chat:** Model select, **深度思考** and **深度研究** checkboxes; **Stop** button during stream (aborts via `AbortController`); per-message **复制** / **Markdown** copy and stats line (token count + 总耗时); Markdown rendering (marked + fallback).
- **Code:** Code Review form with **path** (required), three modes (全 repo / 按 Git 提交 / 当前变更), provider select; for “按 Git 提交”, commit list + **validate-commits** before run, red error message when invalid and run is blocked; streaming logs + Markdown-rendered report; **Review 历史** sidebar list (same style as 最近对话), load/delete saved reviews.
- **Review 历史 title:** List shows **main title** = repo address (git `remote.origin.url` or root path) and **subtitle** = review mode (当前变更 / Git xxx / 按路径 app). Detail view shows full title then file count and provider. Title format: `repo — mode`.
- Fixed layout: sidebar + main scroll; Code Review output and history in sidebar.

### Ops & config

- **`./run`** – Local run with DB wait, `./run help`, optional `SKIP_DB_WAIT`.
- **Config** – `config/app.yaml`, `config/models.yaml`; env substitution; hot reload via `POST /admin/reload`.

---

## Documentation

- **README.md** – Quick start, CLI, config, Chat API summary, testing.
- **docs/API.md** – Full Chat API (request/response, stream format, deep thinking/research).
- **ARCHITECTURE.md** – Components, config, API summary.
- **OPS_GUIDE.md** – Run script, health, reload, troubleshooting.

---

## Tests

- **Chat API:** non-stream, stream, `deep_thinking`, `deep_research`, optional params, usage/duration schema, stream event format, deep_research priority when both flags true.
- **Code Review (runner):** `gather_code_files`, path safety, `validate_commits_for_review` (empty, whitespace, not git repo, mocked valid), `gather_diffs_from_uncommitted` path-outside-root safety, stream error event when runner raises.
- **Code Review (API):** POST /code-review, /code-review/stream (path, commits, uncommitted_only), POST /code-review/validate-commits (empty, not git, valid, 422), GET/POST/DELETE /code-reviews (list, create, delete, limit cap, 404 for invalid id).
- **Web UI:** structure (messages, 深度思考/深度研究/发送/停止, Chat/Code tabs), stream stats and copy buttons in `app.js`, `.msg-stats` in CSS; Code page path required, three review modes, git error area; `app.js` validate-commits call, path/git error display, path check before run.
- **Layout:** no document scroll; sidebar and main scroll areas.
- **Integration:** config, adapters, run script, main API (sessions, search, delete).

---

## Breaking changes

None. New endpoints and optional request fields only.

---

## Recent updates (this branch)

- **Code review title:** History titles use **main = repo address** (git remote origin URL or resolved root path) and **subtitle = review mode** (当前变更 / Git commits / 按路径 …). Backend: `_repo_address()`, `_code_review_subtitle()`, `_code_review_title(root)` in `app/routers/code_review.py`.
- **Web UI:** Review 历史 list renders main title + subtitle (split on ` — `); detail status shows full title then “共 N 个文件, Provider: …”. CSS: `.sidebar-code-reviews .conversation-preview-subtitle`; `.code-review-status` with `white-space: pre-line`.
- **Code quality (from Copilot/Claude review):** `datetime.utcnow` → `datetime.now(timezone.utc)` in models; `SessionStatus` constants in archive; adapter `_with_retry` with exponential backoff; cloud adapter API key validation (clear error when missing); DB URL parsing via `urllib.parse.urlparse`; chat router logs invalid session_id and returns 503 on missing API key.

---

## Migration / notes

- Ensure `config/app.yaml` exists (e.g. from `config/app.yaml.example`) and `database_url` is set.
- For chat, configure a provider (e.g. DashScope) and API key (`dashscope_api_key` or env).
- Web UI is served at `/` when the server is running; static assets under `/static/`.
- Code Review runs under a root directory: set `CODE_REVIEW_ROOT` to the repo path, or leave unset to use the server cwd. For “按 Git 提交” and “当前变更”, the root must be inside a git repository; Copilot/Claude CLI must be available for the chosen provider.
