# Agent Backend

Universal AI tool-calling backend: config-driven chat, embeddings, context compression, and archival. Built for single-user or team use with FastAPI, PostgreSQL (pgvector), and optional Celery/MinIO.

## Features

- **Multi-model config** – YAML-driven embedding and chat providers (DashScope/Qwen, OpenAI, Ollama); hot reload without restart.
- **Chat API** – Streaming and non-streaming completions; session-based; **deep thinking** and **deep research** modes; token usage and duration in response/SSE.
- **Web UI** – Chat with model select, **深度思考** / **深度研究** checkboxes, stream **stop** button, copy/Markdown, and stats (token + duration).
- **Embeddings** – Multi-provider with fallback; pgvector for semantic search over messages.
- **Context compression** – Structured summaries (decision points, todos, code snippets) via configurable strategies.
- **Security** – No secrets in config (`api_key_env` only); CLI adapter whitelist; audit logging.
- **Archival** – Celery task archives by `updated_at` (hot → cold → Parquet/MinIO → delete).

## Entry point

**One CLI and one script:**

| Entry | Use for |
|-------|--------|
| **`agent-backend`** | All commands: serve, configure, test, init-db, Docker start/stop, health, etc. |
| **`./run`** | Run the backend locally with DB auto-start and env loading (no Docker). |

After `pip install -e .`:

```bash
agent-backend --help
```

| Command | Description |
|---------|-------------|
| `agent-backend configure` | Create `config/app.yaml` and `config/models.yaml` from examples if missing |
| `agent-backend serve` | Run the API server (host/port from config/app.yaml; options: `--host`, `--port`, `--reload`) |
| `agent-backend start [SERVICE...]` | Docker: start all or only named service(s) (e.g. `backend`) |
| `agent-backend stop [SERVICE...]` | Docker: stop all or only named service(s) |
| `agent-backend restart [SERVICE...]` | Docker: restart all or only named service(s) |
| `agent-backend init-db` | Create DB extension and tables |
| `agent-backend test` | Run pytest (`--real-api`, `--cov`); real AI tests use ~/.ai_env.sh when present |
| `agent-backend reload-config` | POST /admin/reload |
| `agent-backend archive` | Run Celery archive task once |
| `agent-backend health` | GET /health (readiness check) |
| `agent-backend version` | Print version |
| `agent-backend try-models` | Call each configured chat model (loads ~/.ai_env.sh if present); report OK or error |

**Run script (`./run`)** – single script for local run with DB and env:

| Invocation | Description |
|------------|-------------|
| `./run` or `./run node` | Run backend (start Postgres if needed, wait for DB, then serve) |
| `./run local` | Same as node (for a real local machine) |
| `./run configure` | Create config files from examples if missing |
| `./run docker` or `./run start [SERVICE]` | Docker: start all or named service(s) |
| `./run stop [SERVICE]` | Docker: stop all or named service(s) |
| `./run restart [node\|local\|SERVICE]` | Restart backend (node/local) or Docker service(s) |

**Examples:**

```bash
agent-backend configure             # Create config from examples if missing
agent-backend serve                 # Run server (host/port from config/app.yaml)
agent-backend serve --reload        # Run with auto-reload (dev)
agent-backend start                 # Docker: start all (postgres, redis, minio, backend)
agent-backend start backend        # Docker: start only backend
agent-backend init-db               # Create DB extension and tables
agent-backend test --real-api --cov # Run all tests with real API and coverage
agent-backend health                # GET /health

./run                              # Run backend locally (DB + env + serve)
./run configure                    # Same as agent-backend configure
./run docker                       # Same as agent-backend start
```

## Quick start

### 1. Configure (one-time)

Create app and model config from examples if missing:

```bash
agent-backend configure
# or: ./run configure
```

This creates **`config/app.yaml`** from `config/app.yaml.example` and **`config/models.yaml`** from `config/models.yaml.example` when they don't exist. Edit **`config/app.yaml`** to set `port`, `database_url`, and `dashscope_api_key` (or use `dashscope_api_key: "${DASHSCOPE_API_KEY}"` and set the env; or use `~/.ai_env.sh` — see Testing). Edit `config/models.yaml` for providers and models.

All configuration is loaded from YAML; no `.env` file. Set **`CONFIG_DIR`** to use a different config directory. When you run locally, **`./run`** loads `~/.ai_env.sh` if present (for API keys when not in app.yaml).

### 2. Run with Docker

```bash
agent-backend start        # or: make start  — start all services
agent-backend init-db      # or: make init
# API at http://localhost:8000/docs

# Start/stop/restart only the backend (not postgres/redis/minio):
agent-backend stop backend
agent-backend start backend
agent-backend restart backend
```

### 3. Or run locally (no Docker)

```bash
pip install -e .
agent-backend configure        # Create config/app.yaml and config/models.yaml if missing
# Edit config/app.yaml: database_url, port, dashscope_api_key (or use ~/.ai_env.sh)
./run                          # Loads config and ~/.ai_env.sh if present, waits for Postgres, then serve
# Or: agent-backend serve --reload   for dev auto-reload (no DB auto-start)
```

**Database options when using `./run`:** (1) **`NO_DB_PASSWORD=1 ./run`** if your Postgres has trust auth for localhost; (2) **`USE_LOCAL_POSTGRES=1 ./run`** for project-local Postgres on 5433 (non-root only); (3) **`./run docker`** to run Postgres in Docker. Or run **`./scripts/setup-system-postgres.sh`** (sudo) or set **`DATABASE_URL`**.

## Configuration

Configuration is loaded from **YAML** under the config directory (default: `config/`). No `.env` file.

- **`config/app.yaml`** – Server (`host`, `port`), database, Redis, MinIO, and optional `dashscope_api_key`. Use `config/app.yaml.example` as template. Use `${VAR}` in values to substitute from environment. Also: **`ai_env_path`** (path to shell script for API keys; default `~/.ai_env.sh`; set to `""` to disable); **`skip_db_wait`**, **`no_db_password`**, **`use_local_postgres`**, **`dev`** (options for `./run`; env vars of the same behavior override these).
- **`config/models.yaml`** – Embedding providers, chat providers, default models, and summary strategies. Use `config/models.yaml.example` as template.
- **Chat models** – Under `chat_providers.dashscope.models` list model IDs. Use **`agent-backend try-models`** to verify (API key from `config/app.yaml` or `~/.ai_env.sh`).
- **Substitution** – In any YAML, `${VAR}` or `$VAR` are replaced from the environment.
- **Hot reload** – `POST /admin/reload` or `make reload-config` to reload models config without restart.
- Set **`CONFIG_DIR`** (env) to use a different config directory.

See [ARCHITECTURE.md](ARCHITECTURE.md) for config schema and component overview.

## API summary

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Readiness (DB check) |
| POST | `/admin/reload` | Reload config from disk |
| POST | `/sessions` | Create chat session |
| GET | `/sessions` | List sessions (with first/last preview, topic summary) |
| GET | `/sessions/{id}/messages` | Get messages for a session |
| POST | `/chat` | Chat completion (see [Chat API](#chat-api) below) |
| GET | `/sessions/{id}/search?query=` | Semantic search over session messages |
| DELETE | `/sessions/{id}` | Delete session (cleanup) |

### Chat API

**POST /chat** – Request body: `session_id`, `messages`, optional `stream` (default `true`), `model`, **`deep_thinking`**, **`deep_research`**.

- **Non-stream** (`stream: false`): JSON with `choices`, `usage` (token counts), `duration_ms`.
- **Stream** (`stream: true`): SSE; content events `data: {"choices":[{"delta":{"content":"..."}}]}`; final stats event `data: {"usage":{...},"duration_ms":...}`; then `data: [DONE]`. Client may abort the fetch to stop the stream (e.g. “停止” in Web UI).

**Modes:** `deep_thinking` = step-by-step reasoning (low temperature, larger max_tokens). `deep_research` = researcher-style analysis (same 5-step framework, higher max_tokens). If both are true, deep research is used. See [docs/API.md](docs/API.md) for full request/response schema.

- **Web UI**: **http://localhost:8000** – chat with model select, 深度思考/深度研究, stop button during stream, copy/Markdown, token and duration stats.
- **API docs**: **http://localhost:8000/docs**

## Testing

```bash
agent-backend test
# or: make test   or: pytest tests/ -v
```

Runs the full test suite: CLI adapter, config loader, FastAPI endpoints (mocked), Chat API (stream/non-stream, deep thinking, deep research, usage/duration), Web UI structure, layout, and real-AI integration tests. Real-AI tests (chat, embedding, summarizer) **load `~/.ai_env.sh` automatically** when the file exists; if no API key is set, those tests report a clear error instead of skipping.

### With coverage and real API

```bash
agent-backend test --real-api --cov
# or: make test-real-api
```

With `--real-api`, the CLI loads `~/.ai_env.sh` and sets `RUN_REAL_AI_TESTS=1` so all integration tests run against the real API. Ensure `DASHSCOPE_API_KEY` or `QWEN_API_KEY` is set (e.g. in `~/.ai_env.sh`) and `config/models.yaml` has valid endpoints.

## Makefile targets

| Target | Description |
|--------|-------------|
| `make start [SERVICE=backend]` | Start all or one service |
| `make stop [SERVICE=backend]` | Stop all or one service |
| `make restart [SERVICE=backend]` | Restart all or one service |
| `make up` | Start all services (docker-compose up -d) |
| `make down` | Stop and remove containers |
| `make init` | Create DB, tables, pgvector extension |
| `make test` | Run full test suite (real AI tests load ~/.ai_env.sh when present) |
| `make test-full` | Full suite with coverage |
| `make test-real-api` | Full test; sources ~/.ai_env.sh, maps QWEN_API_KEY → DASHSCOPE_API_KEY |
| `make reload-config` | POST /admin/reload |
| `make archive-now` | Run Celery archive task once |

## Documentation

- **[ARCHITECTURE.md](ARCHITECTURE.md)** – System diagram, components, config, API, archival rules.
- **[docs/API.md](docs/API.md)** – Chat API and sessions API: request/response schema, stream format, deep thinking/research.
- **[OPS_GUIDE.md](OPS_GUIDE.md)** – Quick start, run script, health, config reload, archive task, troubleshooting, security.

## License

See [LICENSE](LICENSE).
