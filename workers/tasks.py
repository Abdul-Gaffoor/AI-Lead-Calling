"""Scheduled work: driving the call queue (MVP sections 6 and 7)."""

import logging

from backend.campaigns.dispatcher import complete_finished_campaigns, dispatch_all
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
