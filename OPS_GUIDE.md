# Ops Guide – Agent Backend

## CLI entry point

All operations can be run via the `agent-backend` command (after `pip install -e .`):

- **`agent-backend configure`** – Create `config/app.yaml` and `config/models.yaml` from examples if missing.
- **`agent-backend serve`** – Run API server (host/port from config/app.yaml; `--reload` for dev).
- **`agent-backend start [SERVICE...]`** – Docker: start all or only named service(s) (e.g. `backend`).
- **`agent-backend stop [SERVICE...]`** – Docker: stop all or only named service(s).
- **`agent-backend restart [SERVICE...]`** – Docker: restart all or only named service(s).
- **`agent-backend init-db`** – Create DB and tables.
- **`agent-backend test`** – Run all 24 tests; real-AI tests load `~/.ai_env.sh` when present. Use **`agent-backend test --real-api --cov`** for coverage.
- **`agent-backend reload-config`** – Hot reload config (default base URL http://localhost:8000).
- **`agent-backend archive`** – Run Celery archive task once.
- **`agent-backend health`** – GET /health (readiness).
- **`agent-backend version`** – Print version.
- **`agent-backend try-models`** – Call each configured chat model (loads `~/.ai_env.sh` if present).

## Configuration

- All configuration is loaded from **YAML** (no `.env` file). **`agent-backend configure`** or **`./run configure`** creates **`config/app.yaml`** from `config/app.yaml.example` and **`config/models.yaml`** from `config/models.yaml.example` when they don’t exist. Edit **`config/app.yaml`** for server (host, port), database_url, and optional **`dashscope_api_key`** (or use `dashscope_api_key: "${DASHSCOPE_API_KEY}"` and set env).
- When you **run** locally, **`./run`** loads **`config/app.yaml`** (for DATABASE_URL wait) and **`~/.ai_env.sh`** if present (for API keys). Set **`CONFIG_DIR`** to use a different config directory.

## Start / stop / restart (Docker)

To operate on **all** services: `agent-backend start`, `agent-backend stop`, `agent-backend restart` (or `make start`, `make stop`, `make restart`).

To operate on **only the backend** (leave postgres, redis, minio running):

```bash
agent-backend stop backend
agent-backend start backend
agent-backend restart backend
# or: make stop SERVICE=backend   make start SERVICE=backend   make restart SERVICE=backend
```

Same with **`./run`**: `./run start backend`, `./run stop backend`, `./run restart backend`. Run `./run` with no args to run the backend **locally** (no Docker).

## Quick start (5 minutes)

1. Run **`agent-backend configure`** or **`./run configure`** to create `config/app.yaml` and `config/models.yaml` from examples if missing; edit `config/app.yaml` (e.g. `dashscope_api_key`, `port`) or use `~/.ai_env.sh` for API keys.
2. Start stack: `make up` or `agent-backend start` (Postgres, Redis, MinIO, backend).
3. Init DB: `make init` or `agent-backend init-db` (create tables + pgvector).
4. Open: http://localhost:8000/docs.

## Run script (`./run`)

- Single entry script for local run: **`./run`** (or **`./run node`** / **`./run local`**). Also supports **`./run configure`**, **`./run docker`**, **`./run stop`**, **`./run restart`**.
- Loads **`config/app.yaml`** (via Python) for `DATABASE_URL` when running locally; loads **`~/.ai_env.sh`** if present (maps `QWEN_API_KEY` → `DASHSCOPE_API_KEY` if needed).
- Waits for PostgreSQL (retry up to 30s); set **`SKIP_DB_WAIT=1`** to skip.
- Runs `alembic upgrade head` if `alembic.ini` exists.
- Runs **`agent-backend serve`** (host/port from config/app.yaml); with **`DEV=1`** uses `--reload`.

## Makefile targets

| Target | Description |
|--------|-------------|
| `make init` | Create DB extension + tables (no Alembic). |
| `make up` | `docker-compose up -d`. |
| `make down` | `docker-compose down`. |
| `make test` | Unit + API tests (no real AI). |
| `make test-full` | Full suite with coverage; set `RUN_REAL_AI_TESTS=1` for real API tests. |
| `make test-real-api` | Full test with real API; sources `~/.ai_env.sh`, maps `QWEN_API_KEY` → `DASHSCOPE_API_KEY`. |
| `make reload-config` | `POST /admin/reload` (hot reload config). |
| `make archive-now` | Trigger Celery archive task once. |

## Config hot reload

- Edit `config/models.yaml` (or the file your app loads).
- Call `POST http://localhost:8000/admin/reload`.
- New requests use the new config without restart. If validation fails, the previous config stays in effect and an error is logged.

## Health and probes

- **GET /health**: Returns 200 with `{"status":"ok","db":"ok"}` when DB is reachable; 503 on failure. Use for Kubernetes readiness/liveness.

## Testing (real API)

The test suite has 24 tests. Real-AI integration tests **load `~/.ai_env.sh` automatically** when the file exists. If no API key is set, those tests report a clear error.

To run with coverage and ensure real API is used:

1. Put your API key in `~/.ai_env.sh`, e.g. `export QWEN_API_KEY=sk-...` or `export DASHSCOPE_API_KEY=sk-...`.
2. Run: `agent-backend test --real-api --cov` or `make test-real-api`.
3. Or run `pytest tests/ -v`; real-AI tests load `~/.ai_env.sh` when present, or report a clear error if no key is set.

## Archive task

- **By schedule**: Configure Celery Beat to run `app.tasks.archive_tasks.archive_by_activity` (e.g. daily).
- **Manual**: `make archive-now` or `celery -A app.tasks.celery_app call app.tasks.archive_tasks.archive_by_activity`.
- Logic: sessions with `updated_at` in 7–180d → cold archive; 180–1095d → Parquet/MinIO; >1095d → safe delete.

## Troubleshooting

| Symptom | Check |
|--------|--------|
| Startup exit 1 | Missing API key: set `dashscope_api_key` in `config/app.yaml` or set env `DASHSCOPE_API_KEY` (or use `~/.ai_env.sh`). |
| 503 on /health | DB not reachable; check `DATABASE_URL`, Postgres health, network. |
| Config not updating | Call `POST /admin/reload`; check logs for validation errors. |
| Embedding/search fails | Check embedding provider keys and `/health`; embedding failure is non-fatal (returns empty). |
| Archive task fails | Check Redis and DB connectivity; ensure `messages_archive` table exists (`make init` or migrations). |

## Security

- Never commit `config/app.yaml` if it contains real API keys; use `config/app.yaml.example` as the template.
- CLI adapter: only whitelisted arguments; denied commands are logged to `audit_logs`.
- All external calls use timeouts; config is validated at load and reload.
