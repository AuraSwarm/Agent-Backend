"""Pydantic schemas for config validation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EmbeddingProviderConfig(BaseModel):
    """Single embedding provider (e.g. DashScope, OpenAI)."""

    api_key_env: str = Field(..., description="Environment variable name for API key")
    endpoint: str = Field(..., description="Embedding API endpoint URL")
    model: str = Field(..., description="Model name")
    dimensions: int = Field(1536, ge=1, le=4096)
    timeout: int = Field(10, ge=1, le=120)


class SummaryOutputSchemaProperty(BaseModel):
    """JSON schema property for summary output validation."""

    type: str = "string"
    # Allow extra for nested object/array definitions
    model_config = {"extra": "allow"}


class ChatProviderConfig(BaseModel):
    """Chat API provider (Qwen/OpenAI-compatible) for summarization and chat."""

    api_key_env: str = Field(..., description="Environment variable for API key")
    endpoint: str = Field(..., description="Chat completions base URL (e.g. .../v1)")
    model: str = Field(..., description="Default model name")
    models: list[str] | None = Field(None, description="Optional list of model IDs to try (e.g. for try-models)")
    timeout: int = Field(60, ge=1, le=120)


class SummaryStrategyConfig(BaseModel):
    """One context compression / summary strategy."""

    model: str = Field(..., description="Model key from chat provider")
    prompt_template: str = Field(..., description="Template with {history} placeholder")
    output_schema: dict[str, Any] = Field(default_factory=dict)


class LocalToolConfig(BaseModel):
    """One local tool: id, name, description, and command (args as list or single string)."""

    id: str = Field(..., description="Unique tool id (e.g. echo, date)")
    name: str = Field(..., description="Display name")
    description: str = Field("", description="What the tool does")
    command: list[str] | str = Field(..., description="Command and args; str is split by shlex; use {key} for params")


class ModelsConfig(BaseModel):
    """Root config for models.yaml: embedding, chat, summary strategies, local tools."""

    embedding_providers: dict[str, EmbeddingProviderConfig] = Field(default_factory=dict)
    default_embedding_provider: str | None = Field(None, alias="default_embedding_provider")
    chat_providers: dict[str, ChatProviderConfig] = Field(default_factory=dict)
    default_chat_provider: str | None = Field(None, alias="default_chat_provider")
    summary_strategies: dict[str, SummaryStrategyConfig] = Field(default_factory=dict)
    local_tools: list[LocalToolConfig] = Field(default_factory=list, description="Registered local tools for GET/POST /tools")

    model_config = {"extra": "forbid", "populate_by_name": True}


class AppSettings(BaseModel):
    """Application settings (loaded from config/app.yaml)."""

    model_config = {"extra": "ignore"}

    host: str = "0.0.0.0"
    port: int = 8000
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_backend"
    redis_url: str = "redis://localhost:6379/0"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "archives"
    config_dir: str = Field(default="config", description="Directory containing app.yaml and models.yaml; CONFIG_DIR env overrides when loading.")
    # API key can be set in YAML or via ${DASHSCOPE_API_KEY}; if set here it is applied to os.environ at load
    dashscope_api_key: str | None = None
    required_env_vars: list[str] = Field(
        default_factory=lambda: ["DASHSCOPE_API_KEY"],
        description="Env vars that must be set if dashscope_api_key is not in config",
    )
    # Path to shell script to source for API keys (e.g. ~/.ai_env.sh). Empty string = do not load; unset = use ~/.ai_env.sh
    ai_env_path: str | None = Field(None, description="Path to env script for API keys; default ~/.ai_env.sh when unset; set to empty to disable")
    # Options for ./run (local run with DB wait). Env vars (SKIP_DB_WAIT, NO_DB_PASSWORD, USE_LOCAL_POSTGRES, DEV) override these.
    skip_db_wait: bool = Field(False, description="Skip waiting for PostgreSQL at startup (./run)")
    no_db_password: bool = Field(False, description="Use Postgres with no password (trust auth) on localhost:5432")
    use_local_postgres: bool = Field(False, description="Use project-local Postgres on port 5433 (.local/postgres-data-5433)")
    dev: bool = Field(False, description="Run server with --reload (./run)")
