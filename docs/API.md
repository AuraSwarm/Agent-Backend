# API Reference

## Overview

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Readiness (DB check) |
| POST | `/admin/reload` | Reload config from disk |
| POST | `/sessions` | Create chat session |
| POST | `/chat` | Chat completion (stream or non-stream) |
| GET | `/sessions` | List sessions (with previews) |
| GET | `/sessions/{id}/messages` | Get messages for a session |
| GET | `/sessions/{id}/search?query=` | Semantic search over session messages |
| DELETE | `/sessions/{id}` | Delete a session (cleanup) |

---

## POST /chat

Chat completion with optional **streaming**, **deep thinking**, and **deep research** modes. Supports token usage and duration statistics in the response.

### Request body

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `session_id` | string (UUID) | yes | — | Session identifier (create via `POST /sessions`). |
| `messages` | array | yes | — | List of `{ "role": "user" \| "assistant" \| "system", "content": "..." }`. |
| `stream` | boolean | no | `true` | If `true`, response is SSE; if `false`, JSON with `choices`/`usage`/`duration_ms`. |
| `model` | string \| null | no | provider default | Override chat model (e.g. `qwen-max`). |
| `deep_thinking` | boolean | no | `false` | Use “深度思考” system prompt: step-by-step reasoning, lower temperature, larger `max_tokens`. |
| `deep_research` | boolean | no | `false` | Use “深度研究” system prompt: researcher-style analysis (background, factors, solutions, pros/cons, conclusion). If both `deep_research` and `deep_thinking` are `true`, **deep research** takes precedence. |

**Example (non-stream):**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "messages": [
    { "role": "user", "content": "如何降低企业碳排放？" }
  ],
  "stream": false,
  "deep_thinking": true
}
```

**Example (stream + deep research):**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "messages": [
    { "role": "user", "content": "请对碳中和路径做深度研究。" }
  ],
  "stream": true,
  "deep_research": true
}
```

### Response: non-stream (`stream: false`)

- **Status:** `200` on success; `503` if no chat provider or API error.
- **Content-Type:** `application/json`.

**Body:**

| Field | Type | Description |
|-------|------|-------------|
| `choices` | array | At least one `{ "message": { "role": "assistant", "content": "..." } }`. |
| `usage` | object | Token usage: `prompt_tokens`, `completion_tokens`, `total_tokens` (when provided by provider). |
| `duration_ms` | number | Server-side duration in milliseconds. |

**Example:**

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "从政策、技术、行为三方面分析..."
      }
    }
  ],
  "usage": {
    "prompt_tokens": 120,
    "completion_tokens": 350,
    "total_tokens": 470
  },
  "duration_ms": 2340.5
}
```

### Response: stream (`stream: true`)

- **Status:** `200` on success; `503` if no chat provider or API error.
- **Content-Type:** `text/event-stream`.

**Event format (SSE):**

- **Content chunks:** `data: {"choices":[{"delta":{"content":"..."}}]}\n\n`
- **Stats (once per stream, before [DONE]):** `data: {"usage":{...},"duration_ms":1234.5}\n\n`
- **End:** `data: [DONE]\n\n`

The client may abort the request (e.g. `AbortController.abort()`) to stop the stream; the server will stop sending data.

**Example (minimal stream):**

```
data: {"choices":[{"delta":{"content":"从"}}]}

data: {"choices":[{"delta":{"content":"政策"}}]}

data: {"choices":[{"delta":{"content":"、技术"}}]}

...

data: {"usage":{"prompt_tokens":120,"completion_tokens":350,"total_tokens":470},"duration_ms":2340.5}

data: [DONE]
```

---

## Deep thinking vs deep research

| Mode | System prompt | Typical params |
|------|----------------|----------------|
| **deep_thinking** | 资深专家，分步骤推理（背景→因素→方案→优缺点→结论） | `temperature=0.2`, `max_tokens=2500`, `top_p=0.6` |
| **deep_research** | 资深研究员，同上五步框架，强调逻辑与细节 | `temperature=0.2`, `max_tokens=3000`, `top_p=0.6` |

Only one system prompt is applied per request: if `deep_research` is `true`, it is used; otherwise `deep_thinking` is used when `true`.

---

## POST /sessions

Create a new chat session.

**Request body (optional):** `{ "title": "optional title" }`

**Response:** `200` with `{ "session_id": "uuid" }`.

---

## GET /sessions

List recent sessions.

**Query:** `limit` (optional, default from config).

**Response:** `200` with array of objects, each with at least: `session_id`, `title`, `updated_at`, `first_message_preview`, `last_message_preview`, `topic_summary`.

---

## GET /sessions/{id}/messages

Get all messages for a session.

**Response:** `200` with array of `{ "role": "user"|"assistant", "content": "..." }`.

---

## GET /sessions/{id}/search

Semantic search over session messages (pgvector).

**Query:** `query` (required), `limit` (optional).

**Response:** `200` with `{ "matches": [...] }`.

---

## DELETE /sessions/{id}

Delete a session and its messages (cleanup). **Response:** `204` or `404` if not found.

---

## Local tools

Tools are defined in **config/models.yaml** under `local_tools`. Each entry has `id`, `name`, `description`, and `command` (list of strings or a single string). Placeholders like `{message}` in `command` are replaced from the request `params`. Arguments are validated (whitelist; no shell metacharacters) before execution.

### GET /tools

List registered local tools.

**Response:** `200` with array of `{"id": "...", "name": "...", "description": "..."}`.

### POST /tools/execute

Execute a registered local tool.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tool_id` | string | yes | Id from GET /tools (e.g. `echo`, `date`). |
| `params` | object | no | Key-value map for command placeholders (e.g. `{"message": "hello"}` for `echo`). |

**Response:** `200` with `{"stdout": "...", "stderr": "...", "returncode": 0}`. On invalid tool id or rejected args: `400`. On execution timeout: `504`.

**Example (echo):**

```bash
curl -X POST http://localhost:8000/tools/execute -H "Content-Type: application/json" -d '{"tool_id":"echo","params":{"message":"hello"}}'
```

---

## Code review

Uses **Copilot** or **Claude** CLI to review code under a path and return a structured report. The server must have `copilot` and/or `claude` on `PATH` (see `./scripts/test-node-integrations.sh`). Path is relative to **CODE_REVIEW_ROOT** (env); if unset, current working directory is used.

### POST /code-review

**Request body:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `path` | string | required | Relative path (e.g. `app`, `app/main.py`) under root. |
| `provider` | string | `"claude"` | `"copilot"` or `"claude"`. |
| `max_files` | int | 200 | Max number of code files to include. |
| `max_total_bytes` | int | 300000 | Max total content size (truncation if exceeded). |
| `timeout_seconds` | int | 180 | CLI run timeout. |

**Response:** `200` with `{"report": "...", "provider": "...", "files_included": N, "stderr": "..."}`. Report is the CLI stdout (Summary, Issues, Suggestions, etc.). On invalid path or provider: `400`. On timeout: `504`.

**Example:**

```bash
export CODE_REVIEW_ROOT=/path/to/repo
curl -X POST http://localhost:8000/code-review -H "Content-Type: application/json" -d '{"path":"app","provider":"claude"}'
```
