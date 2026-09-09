"""Call outcome handling: dispositions, retries and suppression.

This is the logic that runs after every call ends, whether the outcome came
from a telephony webhook or from the AI agent recording a disposition.
"""

import datetime as dt

from sqlalchemy.orm import Session

from backend.auth.models import User
from backend.calls.models import (
    STATE_DISPOSITIONS,
    TERMINAL_DISPOSITIONS,
    CallAttempt,
    Disposition,
)
from backend.campaigns.models import CampaignLead, CampaignLeadStatus
from backend.compliance.service import add_suppression, log_action
from backend.leads.models import Lead, LeadStatus
from backend.telephony.base import ACTIVE_STATES, CallState


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def as_aware(value: dt.datetime | None) -> dt.datetime | None:
    """SQLite drops tzinfo; treat naive timestamps as UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


def apply_call_state(
    db: Session,
    attempt: CallAttempt,
    state: CallState,
    *,
    duration_seconds: int | None = None,
    recording_url: str | None = None,
) -> CallAttempt:
    """Update an attempt from a provider status event.

    Terminal provider states finalize the attempt; a disposition already set
    by the AI agent is never overwritten by the coarser provider state.
    """
    attempt.state = state
    if duration_seconds is not None:
        attempt.duration_seconds = duration_seconds
    if recording_url:
        attempt.recording_url = recording_url

    if state in ACTIVE_STATES:
        return attempt

    attempt.ended_at = utcnow()
    if attempt.disposition is None:
        implied = STATE_DISPOSITIONS.get(state)
        if implied is not None:
            attempt.disposition = implied
        elif state is CallState.COMPLETED:
            # Connected but nobody recorded an outcome — treat as an AI error
            # so it is visible in reporting rather than silently lost.
            attempt.disposition = Disposition.AI_ERROR

    advance_queue_entry(db, attempt)
    return attempt


def record_disposition(
    db: Session,
    attempt: CallAttempt,
    disposition: Disposition,
    *,
    summary: str | None = None,
    ai_payload: dict | None = None,
    callback_at: dt.datetime | None = None,
    actor: User | None = None,
) -> CallAttempt:
    """Record the business outcome of a call (AI agent or human)."""
    attempt.disposition = disposition
    if summary is not None:
        attempt.summary = summary
    if ai_payload is not None:
        attempt.ai_payload = ai_payload
    if attempt.state in ACTIVE_STATES:
        attempt.state = CallState.COMPLETED
        attempt.ended_at = utcnow()

    entry = _queue_entry(db, attempt)
    if entry is not None and callback_at is not None:
        entry.customer_requested_at = callback_at

    # A customer asking not to be called again must be suppressed immediately,
    # with no manual step (MVP section 30).
    if disposition is Disposition.DO_NOT_CALL:
        add_suppression(
            db,
            attempt.to_number,
            reason="OPT_OUT",
            source=f"call:{attempt.id}",
            actor=actor,
        )

    log_action(
        db,
        actor=actor,
        action="CALL_DISPOSITION_RECORDED",
        entity=f"call:{attempt.id}",
        details={"disposition": disposition.value},
    )
    advance_queue_entry(db, attempt)
    return attempt


def _queue_entry(db: Session, attempt: CallAttempt) -> CampaignLead | None:
    if attempt.campaign_lead_id is None:
        return None
    return db.get(CampaignLead, attempt.campaign_lead_id)


def advance_queue_entry(db: Session, attempt: CallAttempt) -> None:
    """Move the campaign queue entry on: retry, exhaust, or complete."""
    entry = _queue_entry(db, attempt)
    if entry is None or entry.status is not CampaignLeadStatus.DIALING:
        return

    disposition = attempt.disposition
    entry.last_disposition = disposition.value if disposition else None
    campaign = entry.campaign

    # An explicit customer callback request always wins over the retry rules.
    if entry.customer_requested_at is not None:
        entry.status = CampaignLeadStatus.PENDING
        entry.next_attempt_at = entry.customer_requested_at
        return

    if disposition is not None and disposition in TERMINAL_DISPOSITIONS:
        entry.status = CampaignLeadStatus.COMPLETED
        entry.next_attempt_at = None
        _close_lead(db, entry)
        return

    delay = campaign.retry_delay_minutes(disposition.value) if disposition else None
    if delay is None:
        entry.status = CampaignLeadStatus.COMPLETED
        entry.next_attempt_at = None
        _close_lead(db, entry)
        return

    if entry.attempts >= campaign.max_attempts:
        entry.status = CampaignLeadStatus.EXHAUSTED
        entry.next_attempt_at = None
        _close_lead(db, entry)
        return

    entry.status = CampaignLeadStatus.PENDING
    entry.next_attempt_at = utcnow() + dt.timedelta(minutes=delay)


def _close_lead(db: Session, entry: CampaignLead) -> None:
    lead = db.get(Lead, entry.lead_id)
    if lead is not None and lead.status is LeadStatus.IN_CAMPAIGN:
        lead.status = LeadStatus.COMPLETED
