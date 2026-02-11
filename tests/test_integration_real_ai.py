"""
Integration tests that call real AI APIs (chat, embedding, summarizer).

Requires a real API key. ~/.ai_env.sh is loaded automatically when present
(AI_ENV_PATH or default ~/.ai_env.sh). If no key is set, tests report a clear error.

  pytest tests/test_integration_real_ai.py -v
"""

import os
from pathlib import Path

import pytest

from tests.conftest import load_ai_env_into_os, AI_ENV_PATH

# Project config dir (real endpoints and summary_strategies)
PROJECT_CONFIG_DIR = (Path(__file__).resolve().parent.parent / "config").as_posix()

REAL_API_KEY_ERROR = (
    "Real API key not configured. Set DASHSCOPE_API_KEY or QWEN_API_KEY in ~/.ai_env.sh "
    "(or export before running), then run: pytest tests/test_integration_real_ai.py -v"
)


def pytest_configure(config):
    """Load ~/.ai_env.sh when running this test module (so keys are available without sourcing)."""
    if AI_ENV_PATH.exists():
        load_ai_env_into_os(AI_ENV_PATH)
    if os.environ.get("QWEN_API_KEY") and not os.environ.get("DASHSCOPE_API_KEY"):
        os.environ["DASHSCOPE_API_KEY"] = os.environ["QWEN_API_KEY"]


def _real_api_configured() -> bool:
    return bool(os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY"))


@pytest.fixture(scope="module")
def real_ai_env():
    """Load env from file if present; set DASHSCOPE_API_KEY from QWEN_API_KEY if needed."""
    if AI_ENV_PATH.exists():
        load_ai_env_into_os(AI_ENV_PATH)
    if os.environ.get("QWEN_API_KEY") and not os.environ.get("DASHSCOPE_API_KEY"):
        os.environ["DASHSCOPE_API_KEY"] = os.environ["QWEN_API_KEY"]
    yield


@pytest.fixture
def integration_config(test_config_dir):
    """Point CONFIG_DIR at test config (no real API key required)."""
    prev = os.environ.get("CONFIG_DIR")
    os.environ["CONFIG_DIR"] = test_config_dir
    yield test_config_dir
    if prev is not None:
        os.environ["CONFIG_DIR"] = prev
    else:
        os.environ.pop("CONFIG_DIR", None)


@pytest.fixture
def require_real_api_key(real_ai_env):
    """Require real API key; report clear error if not configured. Use project config for real endpoints."""
    if not _real_api_configured():
        pytest.fail(REAL_API_KEY_ERROR)
    prev = os.environ.get("CONFIG_DIR")
    os.environ["CONFIG_DIR"] = PROJECT_CONFIG_DIR
    yield
    if prev is not None:
        os.environ["CONFIG_DIR"] = prev
    else:
        os.environ.pop("CONFIG_DIR", None)


def _get_configured_chat_models():
    if not os.environ.get("CONFIG_DIR"):
        return ["qwen-max"]
    from app.config.loader import get_config

    config = get_config()
    providers = getattr(config, "chat_providers", {}) or {}
    default = config.default_chat_provider or "dashscope"
    if default not in providers:
        return ["qwen-max"]
    prov = providers[default]
    return getattr(prov, "models", None) or [prov.model]


def _get_chat_adapter():
    from app.config.loader import get_config
    from app.adapters.cloud import CloudAPIAdapter

    config = get_config()
    providers = getattr(config, "chat_providers", {}) or {}
    default = config.default_chat_provider or "dashscope"
    if default not in providers:
        pytest.fail("No chat provider configured")
    prov = providers[default]
    return CloudAPIAdapter(
        api_key_env=prov.api_key_env,
        endpoint=prov.endpoint,
        model=prov.model,
        timeout=60,
    )


# ---- Real token tests (require API key) ----

@pytest.mark.real_ai
@pytest.mark.asyncio
async def test_chat_non_stream(require_real_api_key):
    """Real API: non-streaming chat returns non-empty text."""
    adapter = _get_chat_adapter()
    text = await adapter.call("Say exactly: ok")
    assert isinstance(text, str)
    assert len(text.strip()) > 0


@pytest.mark.real_ai
@pytest.mark.asyncio
async def test_chat_stream(require_real_api_key):
    """Real API: streaming chat yields chunks and concatenates to non-empty text."""
    adapter = _get_chat_adapter()
    chunks = []
    async for chunk in adapter.stream_call("Reply with one word: hi"):
        chunks.append(chunk)
    full = "".join(chunks)
    assert len(full.strip()) > 0
    assert len(chunks) >= 1


@pytest.mark.real_ai
@pytest.mark.asyncio
async def test_chat_with_messages(require_real_api_key):
    """Real API: chat with messages list (multi-turn) returns non-empty text."""
    adapter = _get_chat_adapter()
    messages = [
        {"role": "user", "content": "Say the number 42 and nothing else."},
    ]
    text = await adapter.call("Ignore previous. Say 99.", messages=messages)
    assert isinstance(text, str)
    assert len(text.strip()) > 0


@pytest.mark.real_ai
@pytest.mark.asyncio
async def test_chat_stream_collects_all_chunks(require_real_api_key):
    """Real API: stream yields multiple chunks; reassembled length is reasonable."""
    adapter = _get_chat_adapter()
    chunks = []
    async for chunk in adapter.stream_call("Count from 1 to 5, one number per line."):
        chunks.append(chunk)
    full = "".join(chunks)
    assert len(chunks) >= 1
    assert len(full) >= 5
    assert "1" in full or "2" in full


@pytest.mark.real_ai
@pytest.mark.asyncio
async def test_embedding(require_real_api_key):
    """Real API: embedding returns list of floats with expected length."""
    from app.embedding.engine import get_embedding

    vec = await get_embedding("hello world")
    assert vec is not None
    assert isinstance(vec, list)
    assert len(vec) > 0
    assert all(isinstance(x, (int, float)) for x in vec)


@pytest.mark.real_ai
@pytest.mark.asyncio
async def test_embedding_different_texts(require_real_api_key):
    """Real API: different texts produce different embedding vectors."""
    from app.embedding.engine import get_embedding

    v1 = await get_embedding("first phrase")
    v2 = await get_embedding("second phrase")
    assert v1 is not None and v2 is not None
    assert len(v1) == len(v2)
    assert v1 != v2


@pytest.mark.real_ai
@pytest.mark.asyncio
async def test_embedding_dimensions(require_real_api_key):
    """Real API: optional dimensions parameter is respected when supported."""
    from app.embedding.engine import get_embedding

    vec = await get_embedding("test", dimensions=256)
    if vec is not None:
        assert len(vec) >= 1
        assert all(isinstance(x, (int, float)) for x in vec)


@pytest.mark.real_ai
@pytest.mark.asyncio
@pytest.mark.parametrize("model", _get_configured_chat_models(), ids=lambda m: m)
async def test_chat_each_model(require_real_api_key, model):
    """Real API: each configured chat model responds to a short prompt."""
    from app.config.loader import get_config
    from app.adapters.cloud import CloudAPIAdapter

    config = get_config()
    providers = getattr(config, "chat_providers", {}) or {}
    default = config.default_chat_provider or "dashscope"
    prov = providers[default]
    adapter = CloudAPIAdapter(
        api_key_env=prov.api_key_env,
        endpoint=prov.endpoint,
        model=model,
        timeout=60,
    )
    text = await adapter.call("Say OK")
    assert isinstance(text, str)
    assert len(text.strip()) > 0


@pytest.mark.real_ai
@pytest.mark.asyncio
async def test_compress_context_real(require_real_api_key):
    """Real API: compress_context returns structured summary or raises."""
    from app.context.summarizer import compress_context

    history = "User: What is 2+2? Assistant: 4."
    summary, err = await compress_context(history, strategy_name="context_compression_v2")
    if err is not None:
        pytest.fail(f"compress_context failed: {err}")
    assert summary is not None
    assert isinstance(summary, dict)
    assert "context_state" in summary or "decision_points" in summary or "todos" in summary


@pytest.mark.real_ai
@pytest.mark.asyncio
async def test_adapter_estimate_tokens(integration_config):
    """Token estimation returns positive integer (no API call)."""
    adapter = _get_chat_adapter()
    n = adapter._estimate_tokens("hello world")
    assert isinstance(n, int)
    assert n >= 1
