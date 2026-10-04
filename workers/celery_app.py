"""Celery application and beat schedule.

Run the worker with:  celery -A workers.celery_app worker --beat
"""

from celery import Celery
from celery.schedules import crontab

from backend.core.config import settings

celery_app = Celery(
    "swaraj",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_max_tasks_per_child=200,
    beat_schedule={
        "dispatch-calls": {
            "task": "workers.tasks.dispatch_calls",
            "schedule": float(settings.dispatch_interval_seconds),
        },
        "complete-campaigns": {
            "task": "workers.tasks.complete_campaigns",
            "schedule": 300.0,
        },
        # Daily is often enough for a retention period measured in days, and it
        # no-ops entirely unless RECORDING_RETENTION_DAYS is set.
        "purge-recordings": {
            "task": "workers.tasks.purge_recordings",
            "schedule": 86400.0,
        },
        # MVP §32. Nightly, in the quiet hours: the calling window is
        # 10:00-18:00 IST, so a dump at 02:00 IST competes with nothing.
        "backup-database": {
            "task": "workers.tasks.backup_database",
            "schedule": crontab(
                hour=(settings.backup_hour_ist - 5) % 24, minute=30
            ),
        },
    },
)
