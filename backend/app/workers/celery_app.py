from datetime import timedelta
from typing import Any

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "ai_paralegal",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

beat_schedule: dict[str, dict[str, Any]] = {}
if settings.retention_sweep_enabled:
    beat_schedule["retention-compliance-sweep"] = {
        "task": "ai_paralegal.retention_sweep",
        "schedule": timedelta(hours=settings.retention_sweep_interval_hours),
        "kwargs": {"dry_run": False},
    }

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    beat_schedule=beat_schedule,
)
