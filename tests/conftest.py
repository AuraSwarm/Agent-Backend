"""Pytest fixtures: test DB, config overrides, mocks, real AI env."""

# Expose team API and UI fixtures to all test modules
pytest_plugins = ["tests.test_team_api", "tests.test_ui_pages"]

import asyncio
import os
import re
from pathlib import Path

# When Web-Service/static exists (e.g. workspace with Aura), serve main UI at / so tests match "Aura started" behavior
_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
_WEB_SERVICE_STATIC = _WORKSPACE_ROOT / "Web-Service" / "static"
if _WEB_SERVICE_STATIC.is_dir():
    os.environ.setdefault("WEB_UI_DIR", str(_WEB_SERVICE_STATIC))

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from memory_base import Base

# Register app-specific models (e.g. CodeReview) with Base.metadata for create_all
from app.storage import models  # noqa: F401

# Path to shell env file used by real_ai tests (never commit this file's contents)
AI_ENV_PATH = Path(os.environ.get("AI_ENV_PATH", os.path.expanduser("~/.ai_env.sh")))


def load_ai_env_into_os(path: Path | None = None) -> bool:
    """
    Parse a shell env file (export VAR=value) and set os.environ.
    Also sets DASHSCOPE_API_KEY from QWEN_API_KEY if present so app config works.
    Returns True if at least one var was set.
    """
    p = path or AI_ENV_PATH
    if not p.exists():
        return False
    pattern = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
    count = 0
    for line in p.read_text().splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        name, value = m.group(1), m.group(2).strip()
        # Remove surrounding quotes
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        os.environ[name] = value
        count += 1
    if count and os.environ.get("QWEN_API_KEY") and not os.environ.get("DASHSCOPE_API_KEY"):
        os.environ["DASHSCOPE_API_KEY"] = os.environ["QWEN_API_KEY"]
    return count > 0


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def test_config_dir(tmp_path):
    """Temporary config dir with minimal models.yaml."""
    (tmp_path / "models.yaml").write_text("""
embedding_providers:
  dashscope:
    api_key_env: "DASHSCOPE_API_KEY"
    endpoint: "https://example.com/embeddings"
    model: "text-embedding-v3"
    dimensions: 1536
    timeout: 10
default_embedding_provider: "dashscope"
chat_providers:
  dashscope:
    api_key_env: "DASHSCOPE_API_KEY"
    endpoint: "https://example.com/v1"
    model: "qwen-max"
    timeout: 60
default_chat_provider: "dashscope"
summary_strategies: {}
""")
    return str(tmp_path)


@pytest.fixture
async def db_session():
    """Test PostgreSQL session; requires TEST_DATABASE_URL."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("TEST_DATABASE_URL must be set for db_session fixture")
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()
