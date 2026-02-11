# Change note – PR to `main`

**Branch:** `sandbox/jy/init_cmt` → `main`

---

## Summary

This PR adds the Agent Backend service (FastAPI, config-driven chat, embeddings, sessions, Web UI) and extends the Chat API with **deep thinking**, **deep research**, stream **usage/duration** stats, and a Web UI **stop** button. Documentation and tests are included.

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

### Web UI

- Chat with model select, **深度思考** and **深度研究** checkboxes.
- **Stop** button during stream (aborts request via `AbortController`).
- Per-message **复制** / **Markdown** copy and stats line (token count + 总耗时).
- Markdown rendering (marked + fallback), fixed layout (sidebar + main scroll).

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
- **Web UI:** structure (messages, 深度思考/深度研究/发送/停止), stream stats and copy buttons in `app.js`, `.msg-stats` in CSS.
- **Layout:** no document scroll; sidebar and main scroll areas.
- **Integration:** config, adapters, run script, main API (sessions, search, delete).

---

## Breaking changes

None. New endpoints and optional request fields only.

---

## Migration / notes

- Ensure `config/app.yaml` exists (e.g. from `config/app.yaml.example`) and `database_url` is set.
- For chat, configure a provider (e.g. DashScope) and API key (`dashscope_api_key` or env).
- Web UI is served at `/` when the server is running; static assets under `/static/`.
