# init: create DB + tables + indexes
# up/start: docker-compose up -d (optionally SERVICE=backend)
# down/stop: docker-compose stop (optionally SERVICE=backend)
# restart: docker-compose restart (optionally SERVICE=backend)
# reload-config: POST /admin/reload
# test: pytest
# archive-now: run Celery archive task

.PHONY: init up down start stop restart reload-config test archive-now

init:
	python -c "\
import asyncio; from app.storage.db import init_db; asyncio.run(init_db()); print('DB and tables ready')"

up:
	docker-compose up -d

down:
	docker-compose down

# Start: all services or only SERVICE (e.g. make start SERVICE=backend)
start:
	@if [ -z "$(SERVICE)" ]; then docker-compose up -d; else docker-compose up -d $(SERVICE); fi

# Stop: all services or only SERVICE (e.g. make stop SERVICE=backend)
stop:
	@if [ -z "$(SERVICE)" ]; then docker-compose stop; else docker-compose stop $(SERVICE); fi

# Restart: all services or only SERVICE (e.g. make restart SERVICE=backend)
restart:
	@if [ -z "$(SERVICE)" ]; then docker-compose restart; else docker-compose restart $(SERVICE); fi

reload-config:
	curl -s -X POST http://localhost:8000/admin/reload

test:
	pytest tests/ -v

# Full local test: unit + API mocks (real AI tests skipped unless RUN_REAL_AI_TESTS=1)
test-full:
	RUN_REAL_AI_TESTS=1 pytest tests/ -v --cov=app --cov-report=term-missing

# Full test with real API: sources ~/.ai_env.sh, maps QWEN_API_KEY -> DASHSCOPE_API_KEY
test-real-api:
	. ~/.ai_env.sh 2>/dev/null || true; \
	export DASHSCOPE_API_KEY="$${QWEN_API_KEY:-$$DASHSCOPE_API_KEY}"; \
	RUN_REAL_AI_TESTS=1 pytest tests/ -v --cov=app --cov-report=term-missing

archive-now:
	celery -A app.tasks.celery_app call app.tasks.archive_tasks.archive_by_activity
