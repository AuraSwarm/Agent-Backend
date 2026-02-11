"""Celery app for archive and async embedding tasks."""

from celery import Celery
from app.config.loader import get_app_settings

settings = get_app_settings()
celery_app = Celery(
    "agent_backend",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.archive_tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
