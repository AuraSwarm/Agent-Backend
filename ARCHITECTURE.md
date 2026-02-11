# Agent Backend – Architecture (V3.0)

## System overview

```mermaid
graph TB
    subgraph "Client"
        A[Backend / API caller] -->|SSE/REST| B(FastAPI Gateway)
    end

    subgraph "Core"
        B --> C[Config Manager – hot reload + validation]
        B --> D[Session Orchestrator]
        D --> E{Tool Router}
        E --> F[Cloud API – Qwen/Claude/OpenAI]
        E --> G[Local – Ollama/vLLM]
        E --> H[CLI – whitelist + audit]
        D --> I[Embedding – multi-provider]
    end

    subgraph "Data"
        J[(PostgreSQL + PGVector)] --> K[Hot: 7d]
        J --> L[Cold: 7–180d]
        J --> M[Deep: 180–1095d]
        N[(MinIO)] --> O[Parquet archive]
        P[(Redis)] --> Q[Session cache / Celery]
    end

    subgraph "Ops"
        R[Celery Beat] --> S[Archive by updated_at]
        R --> T[Async embedding]
        U[Health] --> V[/health]
        W[Config Watcher] --> C
    end
```

## Design principles

- **Config as code**: YAML under `config/` with env substitution; hot reload via `/admin/reload`.
- **Secure by default**: No secrets in config (only `api_key_env`); CLI adapter whitelist; audit log for denials and key actions.
- **Ops-friendly**: `/health` for DB/Redis; `./run` checks env and waits for Postgres; `make init` / `make up` for local run.

## Main components

| Component        | Role |
|-----------------|------|
| **Config**      | Load `config/models.yaml` (embedding + chat providers, summary strategies); Pydantic validation; optional watchdog for hot reload. |
| **Storage**     | SQLAlchemy async (PostgreSQL + pgvector). Tables: `sessions`, `messages` (with embedding), `session_summaries` (JSONB), `audit_logs`, `messages_archive`. |
| **Adapters**    | `BaseToolAdapter`: Cloud (Qwen/OpenAI), Local (Ollama), CLI (whitelist + audit). Timeout and retry in base. |
| **Context**     | `compress_context()`: structured summary (decision_points, todos, code_snippets) via config strategy; Pydantic validation; on failure keep original context + log. |
| **Embedding**   | Multi-provider from config; fallback on failure; returns `None` so main flow is not blocked. |
| **Archive**     | Celery task `archive_by_activity`: 7–180d → `messages_archive`; 180–1095d → Parquet/MinIO; >1095d → safe delete. |

## Config (models.yaml)

- **embedding_providers** / **default_embedding_provider**: used for embeddings and fallback order.
- **chat_providers** / **default_chat_provider**: used for `/chat` and summarization.
- **summary_strategies**: prompt template + output schema (e.g. `context_compression_v2` with `code_snippets`).

## API (summary)

- `GET /health` – readiness (DB check).
- `POST /admin/reload` – reload config from disk.
- `POST /sessions` – create session; `GET /sessions` – list (with first/last preview, topic_summary).
- `GET /sessions/{id}/messages` – messages for session; `DELETE /sessions/{id}` – delete session.
- `POST /chat` – chat completion (stream or not). Body: `session_id`, `messages`, optional `stream`, `model`, **`deep_thinking`**, **`deep_research`**. Response: non-stream returns `choices`, `usage`, `duration_ms`; stream returns SSE with content deltas and a final `usage`/`duration_ms` event. See [docs/API.md](docs/API.md).
- `GET /sessions/{id}/search?query=` – semantic search over session messages (pgvector).

## CLI

Single entry for all usage: **`agent-backend`** (see `agent-backend --help`). Subcommands: `serve`, `init-db`, `test`, `reload-config`, `archive`, `health`, `version`. Use **`agent-backend test --real-api --cov`** for full tests with real API.

## Documentation

- **README.md** – Quick start, CLI, config, API, testing, Makefile.
- **OPS_GUIDE.md** – Operations: CLI, start script, health, reload, archive, troubleshooting.

## Archival rules (by `sessions.updated_at`)

- **Hot**: `updated_at > NOW() - 7 days`.
- **Cold**: 7–180 days → move messages to `messages_archive`, set `status=2`.
- **Deep**: 180–1095 days → export to MinIO (Parquet), set `status=3`.
- **Delete**: >1095 days → audit then set `status=4` (or hard delete per policy).
