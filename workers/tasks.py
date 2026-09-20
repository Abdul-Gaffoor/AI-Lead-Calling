"""Scheduled work: driving the call queue (MVP sections 6 and 7) and
enforcing the recording retention period (section 32)."""

import logging

from backend.calls.recordings import purge_expired_recordings
from backend.campaigns.dispatcher import complete_finished_campaigns, dispatch_all
from backend.core.config import settings
from backend.core.database import SessionLocal
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="workers.tasks.dispatch_calls")
def dispatch_calls() -> dict:
    """Place due calls for every running campaign."""
    with SessionLocal() as db:
        results = dispatch_all(db)
    total = sum(results.values())
    if total:
        logger.info("Dispatched %s call(s): %s", total, results)
    return {"placed": total, "by_campaign": results}


@celery_app.task(name="workers.tasks.complete_campaigns")
def complete_campaigns() -> dict:
    """Close out campaigns whose queues are fully worked."""
    with SessionLocal() as db:
        count = complete_finished_campaigns(db)
    return {"completed": count}


@celery_app.task(name="workers.tasks.purge_recordings")
def purge_recordings() -> dict:
    """Delete call audio past the retention period.

    Off unless RECORDING_RETENTION_DAYS is set: how long customer voice data
    may be kept is a decision for Swaraj, not a default this code should pick.
    """
    with SessionLocal() as db:
        deleted = purge_expired_recordings(
            db, older_than_days=settings.recording_retention_days
        )
        db.commit()
    return {"deleted": deleted}
