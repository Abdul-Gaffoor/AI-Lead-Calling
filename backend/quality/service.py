"""Assembling a call for review, and counting what reviewers found.

MVP section 29 asks for the whole call in one place — recording, transcript,
what the AI extracted, its summary, the score and the disposition — because a
reviewer cannot judge a lead score without seeing what the customer actually
said. Section 38 then measures the platform on exactly the faults recorded
here, so the counts are the point, not a by-product.
"""

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.ai.models import Conversation, ConversationTurn
from backend.auth.models import User
from backend.calls.models import CallAttempt, Disposition
from backend.customers.models import Customer
from backend.leads.models import Lead
from backend.quality.models import CallReview, ReviewFlag, ReviewVerdict


class QualityError(Exception):
    """Raised when a call cannot be reviewed."""


#: Only calls that actually reached a conversation are worth a reviewer's time;
#: there is nothing to judge in a call that was never answered.
REVIEWABLE_DISPOSITIONS = frozenset(Disposition) - {
    Disposition.NO_ANSWER,
    Disposition.BUSY,
    Disposition.SWITCHED_OFF,
    Disposition.TELEPHONY_ERROR,
}


def reviewable_calls(
    db: Session,
    *,
    reviewed: bool | None = None,
    disposition: Disposition | None = None,
    since: dt.date | None = None,
    limit: int = 50,
) -> list[dict]:
    """The review queue: answered calls, newest first."""
    review_count = (
        select(CallReview.call_attempt_id, func.count(CallReview.id).label("reviews"))
        .group_by(CallReview.call_attempt_id)
        .subquery()
    )

    statement = (
        select(CallAttempt, Customer, func.coalesce(review_count.c.reviews, 0))
        .join(Customer, Customer.id == CallAttempt.customer_id)
        .outerjoin(review_count, review_count.c.call_attempt_id == CallAttempt.id)
        .where(CallAttempt.disposition.in_(tuple(REVIEWABLE_DISPOSITIONS)))
        .order_by(CallAttempt.started_at.desc())
    )
    if disposition is not None:
        statement = statement.where(CallAttempt.disposition == disposition)
    if since is not None:
        statement = statement.where(
            CallAttempt.started_at >= dt.datetime.combine(since, dt.time.min, dt.timezone.utc)
        )
    if reviewed is True:
        statement = statement.where(review_count.c.reviews > 0)
    elif reviewed is False:
        statement = statement.where(review_count.c.reviews.is_(None))

    rows = db.execute(statement.limit(limit)).all()
    return [
        {
            "call_id": attempt.id,
            "customer_name": customer.name,
            "phone": customer.phone,
            "started_at": attempt.started_at,
            "duration_seconds": attempt.duration_seconds,
            "disposition": attempt.disposition,
            "lead_score": (attempt.ai_payload or {}).get("lead_score"),
            "classification": (attempt.ai_payload or {}).get("classification"),
            "has_recording": attempt.recording is not None,
            "review_count": int(reviews or 0),
        }
        for attempt, customer, reviews in rows
    ]


def call_for_review(db: Session, attempt: CallAttempt) -> dict:
    """Everything a reviewer needs about one call (MVP section 29)."""
    customer = db.get(Customer, attempt.customer_id)
    lead = db.get(Lead, attempt.lead_id) if attempt.lead_id else None
    conversation = db.scalar(
        select(Conversation).where(Conversation.call_attempt_id == attempt.id)
    )

    transcript = []
    if conversation is not None:
        turns = db.scalars(
            select(ConversationTurn)
            .where(ConversationTurn.conversation_id == conversation.id)
            .order_by(ConversationTurn.index)
        ).all()
        transcript = [
            {
                "index": turn.index,
                "speaker": turn.speaker.value,
                "text": turn.text,
                "language": turn.language,
                "latency_ms": turn.latency_ms,
            }
            for turn in turns
        ]

    payload = attempt.ai_payload or {}
    reviews = db.scalars(
        select(CallReview).where(CallReview.call_attempt_id == attempt.id)
    ).all()

    return {
        "call_id": attempt.id,
        "customer_name": customer.name if customer else None,
        "phone": customer.phone if customer else None,
        "city": lead.city if lead else None,
        "started_at": attempt.started_at,
        "ended_at": attempt.ended_at,
        "duration_seconds": attempt.duration_seconds,
        "direction": attempt.direction,
        "disposition": attempt.disposition,
        "language": conversation.language if conversation else None,
        "service": conversation.service if conversation else None,
        "transcript": transcript,
        "extracted": conversation.collected if conversation else {},
        "summary": attempt.summary or (conversation.summary if conversation else None),
        "lead_score": payload.get("lead_score"),
        "classification": payload.get("classification"),
        "ai_payload": payload,
        "has_recording": attempt.recording is not None,
        "recording_seconds": attempt.recording.duration_seconds if attempt.recording else None,
        "reviews": [
            {
                "id": review.id,
                "reviewer_id": review.reviewer_id,
                "verdict": review.verdict,
                "flags": review.flags or [],
                "note": review.note,
                "created_at": review.created_at,
            }
            for review in reviews
        ],
    }


def submit_review(
    db: Session,
    attempt: CallAttempt,
    reviewer: User,
    *,
    verdict: ReviewVerdict,
    flags: list[ReviewFlag] | None = None,
    note: str | None = None,
) -> CallReview:
    """Record (or replace) this reviewer's verdict on this call."""
    flags = list(flags or [])
    if verdict is ReviewVerdict.INCORRECT and not flags:
        # An "incorrect" with no fault named cannot be counted, and counting is
        # the whole point of collecting these.
        raise QualityError("Say what was wrong: an incorrect call needs at least one flag")
    if verdict is ReviewVerdict.CORRECT and flags:
        raise QualityError("A call marked correct cannot also carry fault flags")

    review = db.scalar(
        select(CallReview).where(
            CallReview.call_attempt_id == attempt.id,
            CallReview.reviewer_id == reviewer.id,
        )
    )
    if review is None:
        review = CallReview(call_attempt_id=attempt.id, reviewer_id=reviewer.id)
        db.add(review)

    review.verdict = verdict
    review.flags = [flag.value for flag in flags]
    review.note = note
    db.flush()
    return review


def quality_summary(db: Session, *, since: dt.date | None = None) -> dict:
    """What reviewers are finding — the AI-quality process's input.

    Reported as counts and a rate rather than a single score: "23% of reviewed
    calls had a wrong lead score" is actionable, an overall grade is not.
    """
    statement = select(CallReview)
    if since is not None:
        statement = statement.where(
            CallReview.created_at >= dt.datetime.combine(since, dt.time.min, dt.timezone.utc)
        )
    reviews = db.scalars(statement).all()

    total = len(reviews)
    correct = sum(1 for review in reviews if review.verdict is ReviewVerdict.CORRECT)
    flag_counts = {flag.value: 0 for flag in ReviewFlag}
    for review in reviews:
        for flag in review.flags or []:
            if flag in flag_counts:
                flag_counts[flag] += 1

    reviewed_calls = len({review.call_attempt_id for review in reviews})
    answered = db.scalar(
        select(func.count(CallAttempt.id)).where(
            CallAttempt.disposition.in_(tuple(REVIEWABLE_DISPOSITIONS))
        )
    ) or 0

    return {
        "reviews": total,
        "reviewed_calls": reviewed_calls,
        "reviewable_calls": answered,
        # How much of the AI's work anyone has actually checked. A high
        # accuracy over four reviewed calls means very little.
        "coverage_percent": round(reviewed_calls / answered * 100, 1) if answered else 0.0,
        "correct": correct,
        "incorrect": total - correct,
        "accuracy_percent": round(correct / total * 100, 1) if total else 0.0,
        "flags": flag_counts,
    }
