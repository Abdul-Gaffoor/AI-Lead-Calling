import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user
from backend.auth.models import User
from backend.calls.inbound import identify_caller, register_inbound_call
from backend.calls.models import CallAttempt, Disposition
from backend.calls.schemas import CallOut, DispositionRequest, WebhookAck
from backend.calls.service import apply_call_state, record_disposition
from backend.core.config import settings
from backend.core.database import get_db
from backend.leads.phone import normalize_indian_mobile
from backend.telephony.factory import get_telephony_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calls", tags=["calls"])


@router.get("", response_model=list[CallOut])
def list_calls(
    campaign_id: int | None = Query(default=None),
    lead_id: int | None = Query(default=None),
    disposition: Disposition | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(CallAttempt).order_by(CallAttempt.id.desc())
    if campaign_id is not None:
        stmt = stmt.where(CallAttempt.campaign_id == campaign_id)
    if lead_id is not None:
        stmt = stmt.where(CallAttempt.lead_id == lead_id)
    if disposition is not None:
        stmt = stmt.where(CallAttempt.disposition == disposition)
    return db.scalars(stmt.limit(limit).offset(offset)).all()


@router.get("/{call_id}", response_model=CallOut)
def get_call(
    call_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    attempt = db.get(CallAttempt, call_id)
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")
    return attempt


@router.post("/{call_id}/disposition", response_model=CallOut)
def set_disposition(
    call_id: int,
    payload: DispositionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record a call's business outcome.

    Used by the AI agent at the end of a conversation (Sprint 3) and by
    executives correcting an outcome by hand.
    """
    attempt = db.get(CallAttempt, call_id)
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")
    record_disposition(
        db,
        attempt,
        payload.disposition,
        summary=payload.summary,
        ai_payload=payload.ai_payload,
        callback_at=payload.callback_at,
        actor=current_user,
    )
    db.commit()
    db.refresh(attempt)
    return attempt


@router.post("/webhooks/{provider_name}", response_model=WebhookAck)
async def telephony_webhook(
    provider_name: str,
    request: Request,
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Call status callback from the telephony provider.

    Public endpoint: authenticated with a shared token rather than a user
    session, since the provider calls it. Always returns 200 for unknown
    calls so the provider does not retry forever.
    """
    if settings.telephony_webhook_token:
        supplied = token or request.headers.get("X-Webhook-Token")
        if supplied != settings.telephony_webhook_token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook token")

    provider = get_telephony_provider()
    if provider_name != provider.name:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No active provider named {provider_name}"
        )

    payload: dict = {}
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
    else:
        payload = dict(await request.form())

    event = provider.parse_webhook(payload)

    attempt = None
    if event.provider_call_id:
        attempt = db.scalar(
            select(CallAttempt).where(CallAttempt.provider_call_id == event.provider_call_id)
        )
    if attempt is None and event.call_reference and event.call_reference.isdigit():
        attempt = db.get(CallAttempt, int(event.call_reference))

    if attempt is None:
        logger.warning(
            "Telephony webhook for unknown call: provider_call_id=%r reference=%r",
            event.provider_call_id,
            event.call_reference,
        )
        return WebhookAck(received=True, call_id=None)

    if attempt.provider_call_id is None and event.provider_call_id:
        attempt.provider_call_id = event.provider_call_id

    apply_call_state(
        db,
        attempt,
        event.state,
        duration_seconds=event.duration_seconds,
        recording_url=event.recording_url,
    )
    db.commit()
    return WebhookAck(received=True, call_id=attempt.id)


@router.get("/inbound/identify")
def identify_inbound_caller(
    phone: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Look up an inbound caller's history (MVP section 24)."""
    normalized = normalize_indian_mobile(phone)
    if normalized is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Not a valid Indian mobile number")
    return identify_caller(db, normalized)


@router.post("/inbound", response_model=dict, status_code=status.HTTP_201_CREATED)
async def inbound_call(
    request: Request,
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Register an inbound call from the telephony provider.

    Returns the call id plus the caller's previous context so the AI (or a
    human) continues the conversation rather than starting fresh.
    """
    if settings.telephony_webhook_token:
        supplied = token or request.headers.get("X-Webhook-Token")
        if supplied != settings.telephony_webhook_token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook token")

    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
    else:
        payload = dict(await request.form())

    raw_number = (
        payload.get("From") or payload.get("from") or payload.get("CallFrom") or ""
    )
    phone = normalize_indian_mobile(str(raw_number))
    if phone is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Inbound call had no usable caller number"
        )

    provider = get_telephony_provider()
    attempt, context = register_inbound_call(
        db,
        phone=phone,
        provider=provider.name,
        provider_call_id=str(payload.get("CallSid") or payload.get("call_id") or "") or None,
    )
    db.commit()
    return {"call_id": attempt.id, "context": context}
