"""Call queue dispatcher (MVP sections 6 and 7).

Each tick, for every running campaign: check the calling window, work out how
many concurrency slots are free, pick the leads that are due, and place calls
through the configured telephony provider.
"""

import datetime as dt
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.calls.models import CallAttempt, Disposition
from backend.calls.service import advance_queue_entry, as_aware, utcnow
from backend.campaigns.models import Campaign, CampaignLead, CampaignLeadStatus, CampaignStatus
from backend.compliance.service import is_suppressed
from backend.core.config import settings
from backend.customers.models import Customer
from backend.leads.models import Lead
from backend.telephony.base import ACTIVE_STATES, CallRequest, CallState, TelephonyError
from backend.telephony.factory import get_telephony_provider

logger = logging.getLogger(__name__)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown timezone %r; falling back to UTC", name)
        return ZoneInfo("UTC")


def is_within_calling_window(campaign: Campaign, now: dt.datetime | None = None) -> bool:
    """Is `now` inside the campaign's permitted calling hours?"""
    now = now or utcnow()
    local = now.astimezone(_zone(campaign.timezone))

    if campaign.start_date is not None and local.date() < campaign.start_date:
        return False

    start, end, current = campaign.window_start, campaign.window_end, local.time()
    if start <= end:
        return start <= current < end
    # Window wraps past midnight
    return current >= start or current < end


def in_flight_count(db: Session, campaign_id: int) -> int:
    return (
        db.scalar(
            select(func.count(CallAttempt.id)).where(
                CallAttempt.campaign_id == campaign_id,
                CallAttempt.state.in_(ACTIVE_STATES),
            )
        )
        or 0
    )


def reclaim_stuck_calls(db: Session, campaign_id: int | None = None) -> int:
    """Fail out calls whose status callback never arrived.

    A call only leaves an active state when the provider says so. If that
    callback is lost — a dropped webhook, a misconfigured PUBLIC_BASE_URL, a
    provider incident — the attempt counts against concurrency forever. At a
    concurrency of five, five lost callbacks stop the campaign dialling for
    good, silently and permanently.

    Each reclaimed attempt goes back through the normal retry path, so the
    lead is tried again rather than quietly dropped.
    """
    if settings.stuck_call_timeout_minutes <= 0:
        return 0

    cutoff = utcnow() - dt.timedelta(minutes=settings.stuck_call_timeout_minutes)
    statement = select(CallAttempt).where(
        CallAttempt.state.in_(ACTIVE_STATES),
        CallAttempt.started_at < cutoff,
    )
    if campaign_id is not None:
        statement = statement.where(CallAttempt.campaign_id == campaign_id)

    reclaimed = 0
    for attempt in db.scalars(statement):
        # as_aware: SQLite hands back naive datetimes.
        if as_aware(attempt.started_at) >= cutoff:
            continue
        logger.warning(
            "Call %s stuck in %s since %s with no status callback; failing it out",
            attempt.id,
            attempt.state.value,
            attempt.started_at,
        )
        attempt.state = CallState.FAILED
        if attempt.disposition is None:
            attempt.disposition = Disposition.TELEPHONY_ERROR
        attempt.ended_at = utcnow()
        attempt.summary = (
            attempt.summary
            or f"No status callback within {settings.stuck_call_timeout_minutes} minutes"
        )
        advance_queue_entry(db, attempt)
        reclaimed += 1

    if reclaimed:
        db.flush()
    return reclaimed


def due_entries(db: Session, campaign: Campaign, limit: int) -> list[CampaignLead]:
    now = utcnow()
    stmt = (
        select(CampaignLead)
        .where(
            CampaignLead.campaign_id == campaign.id,
            CampaignLead.status == CampaignLeadStatus.PENDING,
            CampaignLead.attempts < campaign.max_attempts,
        )
        .order_by(CampaignLead.next_attempt_at.asc().nulls_first(), CampaignLead.id)
        .limit(limit * 3)  # over-fetch: some may be filtered out below
    )
    entries = []
    for entry in db.scalars(stmt):
        due_at = as_aware(entry.next_attempt_at)
        if due_at is not None and due_at > now:
            continue
        entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


def dispatch_campaign(db: Session, campaign: Campaign) -> int:
    """Place as many calls as the campaign's free concurrency allows.

    Returns the number of calls started.
    """
    if campaign.status is not CampaignStatus.RUNNING:
        return 0
    if not is_within_calling_window(campaign):
        return 0

    # Before counting free slots, release any the provider never told us about.
    reclaim_stuck_calls(db, campaign.id)

    slots = campaign.concurrency - in_flight_count(db, campaign.id)
    if slots <= 0:
        return 0

    provider = get_telephony_provider()
    placed = 0

    for entry in due_entries(db, campaign, slots):
        lead = db.get(Lead, entry.lead_id)
        customer = db.get(Customer, lead.customer_id) if lead else None
        if lead is None or customer is None:
            entry.status = CampaignLeadStatus.CANCELLED
            continue

        # Safety net: never dial a number that has opted out since being queued.
        if customer.opted_out or is_suppressed(db, customer.phone):
            entry.status = CampaignLeadStatus.SUPPRESSED
            entry.next_attempt_at = None
            continue

        entry.attempts += 1
        entry.status = CampaignLeadStatus.DIALING
        attempt = CallAttempt(
            campaign_id=campaign.id,
            campaign_lead_id=entry.id,
            lead_id=lead.id,
            customer_id=customer.id,
            attempt_number=entry.attempts,
            provider=provider.name,
            to_number=customer.phone,
            from_number=settings.exotel_caller_id or None,
            state=CallState.QUEUED,
        )
        db.add(attempt)
        db.flush()  # assign attempt.id for the call reference

        callback_url = (
            f"{settings.public_base_url.rstrip('/')}/calls/webhooks/{provider.name}"
            if settings.public_base_url
            else None
        )
        try:
            result = provider.place_call(
                CallRequest(
                    to_number=customer.phone,
                    call_reference=str(attempt.id),
                    from_number=attempt.from_number,
                    status_callback_url=callback_url,
                )
            )
        except TelephonyError as exc:
            logger.warning("Failed to place call for lead %s: %s", lead.id, exc)
            attempt.state = CallState.FAILED
            attempt.disposition = Disposition.TELEPHONY_ERROR
            attempt.ended_at = utcnow()
            attempt.summary = str(exc)
            # Reuse the standard retry path for this failure.
            advance_queue_entry(db, attempt)
            continue

        attempt.provider_call_id = result.provider_call_id
        attempt.state = result.state
        placed += 1

    db.commit()
    return placed


def dispatch_all(db: Session) -> dict[int, int]:
    """Run one dispatch tick across every running campaign."""
    campaigns = db.scalars(
        select(Campaign)
        .where(Campaign.status == CampaignStatus.RUNNING)
        .order_by(Campaign.priority.desc(), Campaign.id)
    ).all()

    results: dict[int, int] = {}
    for campaign in campaigns:
        try:
            results[campaign.id] = dispatch_campaign(db, campaign)
        except Exception:  # one bad campaign must not stop the others
            db.rollback()
            logger.exception("Dispatch failed for campaign %s", campaign.id)
            results[campaign.id] = 0
    return results


def complete_finished_campaigns(db: Session) -> int:
    """Mark running campaigns whose queue is fully worked as COMPLETED."""
    completed = 0
    campaigns = db.scalars(
        select(Campaign).where(Campaign.status == CampaignStatus.RUNNING)
    ).all()
    for campaign in campaigns:
        remaining = db.scalar(
            select(func.count(CampaignLead.id)).where(
                CampaignLead.campaign_id == campaign.id,
                CampaignLead.status.in_(
                    (CampaignLeadStatus.PENDING, CampaignLeadStatus.DIALING)
                ),
            )
        )
        total = db.scalar(
            select(func.count(CampaignLead.id)).where(CampaignLead.campaign_id == campaign.id)
        )
        if total and not remaining:
            campaign.status = CampaignStatus.COMPLETED
            campaign.completed_at = utcnow()
            completed += 1
    if completed:
        db.commit()
    return completed
