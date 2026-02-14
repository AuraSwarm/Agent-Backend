"""Tool adapters: Cloud API, Local model, CLI (with whitelist + audit)."""

from app.adapters.base import BaseToolAdapter
from app.adapters.factory import build_chat_adapter

__all__ = ["BaseToolAdapter", "build_chat_adapter"]
