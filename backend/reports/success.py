"""The MVP section 38 success criteria, measured.

Section 38 is blunt about why this exists:

    Don't judge the project by "AI sounds impressive."

So it lists eleven things to measure and asks for a comparison between the
current human process and the AI-assisted one. This module produces those
eleven from call records rather than from anyone's impression.

Two rules it holds to.

**Nothing is reported without its inputs.** Cost per qualified lead is a
rate multiplied by a duration; hours saved is a baseline multiplied by a
count. Both are only as good as numbers nobody has signed off yet, so
every derived figure comes back with the assumptions that produced it, the
same way `solar_engine` does. A number on a dashboard with no provenance
gets quoted in a board meeting.

**A measure with no data says so.** Returning 0.0 for a rate with an empty
denominator is a lie that looks like a fact — "0% qualification" reads as
failure when it means "no calls yet". Those come back as None with a
reason attached.

Three of the eleven come from the evaluation harness rather than from
production traffic (Telugu understanding, field capture, escalation
accuracy); `backend/evaluation/` owns those and this module points at it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.ai.models import Conversation, ConversationTurn, Speaker
from backend.calls.models import CallAttempt, Disposition
from backend.core.config import settings
from backend.customers.models import Customer

#: Dispositions that mean a person was actually reached and talked to.
CONNECTED_DISPOSITIONS = frozenset(
    {
        Disposition.QUALIFIED_HOT,
        Disposition.QUALIFIED_WARM,
        Disposition.QUALIFIED_COLD,
        Disposition.SITE_SURVEY_REQUESTED,
        Disposition.CALLBACK_REQUESTED,
        Disposition.HUMAN_TRANSFER,
        Disposition.NOT_INTERESTED,
        Disposition.EXISTING_CUSTOMER,
        Disposition.SERVICE_REQUEST,
        Disposition.DO_NOT_CALL,
    }
)

QUALIFIED_DISPOSITIONS = frozenset(
    {
        Disposition.QUALIFIED_HOT,
        Disposition.QUALIFIED_WARM,
        Disposition.SITE_SURVEY_REQUESTED,
        Disposition.HUMAN_TRANSFER,
    }
)

#: Measures the harness owns, not production traffic.
FROM_EVALUATION_HARNESS = (
    "Telugu understanding accuracy",
    "Required-field capture accuracy",
    "Lead-classification accuracy",
    "Human escalation accuracy",
)


@dataclass
class Measure:
    """One number, or an honest account of why there isn't one."""

    name: str
    value: float | None = None
    unit: str = ""
    sample: int = 0
    unavailable: str | None = None
    assumptions: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        payload = {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "sample": self.sample,
        }
        if self.unavailable:
            payload["unavailable"] = self.unavailable
        if self.assumptions:
            payload["assumptions"] = self.assumptions
        return payload


def _rate(numerator: int, denominator: int, name: str, unit: str = "%") -> Measure:
    """A percentage, or None when there is nothing to divide by."""
    if denominator <= 0:
        return Measure(
            name=name,
            unit=unit,
            sample=0,
            unavailable="no calls in this period yet",
        )
    return Measure(
        name=name,
        value=round(numerator / denominator * 100, 1),
        unit=unit,
        sample=denominator,
    )


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def success_criteria(db: Session, *, since: dt.date | None = None) -> dict:
    """Every MVP §38 measure that production traffic can answer."""
    window_start = (
        dt.datetime.combine(since, dt.time.min, tzinfo=dt.timezone.utc)
        if since
        else None
    )

    def scoped(statement):
        return statement.where(CallAttempt.created_at >= window_start) if window_start else statement

    attempted = db.scalar(scoped(select(func.count(CallAttempt.id)))) or 0
    connected = db.scalar(
        scoped(
            select(func.count(CallAttempt.id)).where(
                CallAttempt.disposition.in_(tuple(CONNECTED_DISPOSITIONS))
            )
        )
    ) or 0
    qualified = db.scalar(
        scoped(
            select(func.count(CallAttempt.id)).where(
                CallAttempt.disposition.in_(tuple(QUALIFIED_DISPOSITIONS))
            )
        )
    ) or 0
    surveys_requested = db.scalar(
        scoped(
            select(func.count(CallAttempt.id)).where(
                CallAttempt.disposition == Disposition.SITE_SURVEY_REQUESTED
            )
        )
    ) or 0
    opted_out = db.scalar(
        scoped(
            select(func.count(CallAttempt.id)).where(
                CallAttempt.disposition == Disposition.DO_NOT_CALL
            )
        )
    ) or 0
    total_seconds = int(
        db.scalar(scoped(select(func.coalesce(func.sum(CallAttempt.duration_seconds), 0)))) or 0
    )
    with_duration = db.scalar(
        scoped(
            select(func.count(CallAttempt.id)).where(
                CallAttempt.duration_seconds.isnot(None)
            )
        )
    ) or 0

    measures: list[Measure] = [
        _rate(connected, attempted, "Call connection rate"),
        _rate(qualified, connected, "Qualification rate"),
        _rate(surveys_requested, qualified, "Site-survey conversion"),
        # Opt-out rate is measured against conversations that happened, not
        # against dials. A number that never answered cannot have opted out,
        # and counting it would make a campaign look gentler the worse its
        # connection rate got.
        _rate(opted_out, connected, "Customer opt-out rate"),
        _ai_latency(db, window_start),
        _average_duration(total_seconds, with_duration),
        _cost_per_qualified_lead(total_seconds, qualified),
        _hours_saved(connected, attempted),
    ]

    return {
        "since": since.isoformat() if since else None,
        "totals": {
            "attempted": attempted,
            "connected": connected,
            "qualified": qualified,
            "site_surveys_requested": surveys_requested,
            "opted_out": opted_out,
            "ai_minutes": round(total_seconds / 60, 1),
        },
        "measures": [m.as_dict() for m in measures],
        "from_evaluation_harness": {
            "measures": list(FROM_EVALUATION_HARNESS),
            "how": "python -m backend.cli evaluate --all",
            "why": (
                "Accuracy needs a known-correct answer to compare against. "
                "Production calls have no ground truth; the §37 corpus does."
            ),
        },
        "comparison": _comparison(connected, total_seconds),
    }


def _ai_latency(db: Session, window_start: dt.datetime | None) -> Measure:
    """How long the AI took to reply, from real calls (MVP §38).

    Measured on the AI's own turns: the orchestrator records the time from
    receiving the customer's words to having a reply ready.
    """
    statement = select(ConversationTurn.latency_ms).where(
        ConversationTurn.speaker == Speaker.AI,
        ConversationTurn.latency_ms.isnot(None),
    )
    if window_start is not None:
        statement = statement.where(ConversationTurn.created_at >= window_start)

    values = [int(v) for v in db.scalars(statement).all() if v is not None]
    if not values:
        return Measure(
            name="AI response latency",
            unit="ms",
            unavailable="no AI turns recorded yet",
        )
    return Measure(
        name="AI response latency",
        value=_percentile(values, 0.5),
        unit="ms (p50)",
        sample=len(values),
        assumptions={
            "p95_ms": _percentile(values, 0.95),
            "max_ms": max(values),
            "measured": "customer utterance received to reply ready",
            "excludes": "telephony and network transit",
        },
    )


def _average_duration(total_seconds: int, with_duration: int) -> Measure:
    if with_duration <= 0:
        return Measure(
            name="Average call duration",
            unit="s",
            unavailable="no completed calls with a duration yet",
        )
    return Measure(
        name="Average call duration",
        value=round(total_seconds / with_duration, 1),
        unit="s",
        sample=with_duration,
    )


def _cost_per_qualified_lead(total_seconds: int, qualified: int) -> Measure:
    """What a qualified lead costs to produce (MVP §38).

    Rate times duration. The rates are placeholders until somebody puts
    the real invoices next to them, so they travel with the answer — this
    is the same treatment `solar_engine` gives its constants, and for the
    same reason: a figure like this ends up in a board pack.
    """
    minutes = total_seconds / 60
    per_minute = (
        settings.telephony_cost_per_minute
        + settings.ai_cost_per_minute
    )
    assumptions = {
        "telephony_cost_per_minute": settings.telephony_cost_per_minute,
        "ai_cost_per_minute": settings.ai_cost_per_minute,
        "currency": "INR",
        "ai_minutes": round(minutes, 1),
        "status": "PLACEHOLDER rates — replace with the real invoices",
    }

    if qualified <= 0:
        return Measure(
            name="Cost per qualified lead",
            unit="INR",
            unavailable="no qualified leads in this period yet",
            assumptions=assumptions,
        )
    if per_minute <= 0:
        return Measure(
            name="Cost per qualified lead",
            unit="INR",
            unavailable="no per-minute costs configured",
            assumptions=assumptions,
        )
    return Measure(
        name="Cost per qualified lead",
        value=round(minutes * per_minute / qualified, 2),
        unit="INR",
        sample=qualified,
        assumptions=assumptions,
    )


def _hours_saved(connected: int, attempted: int) -> Measure:
    """Executive hours the AI did not need a person for (MVP §38).

    Counts every dial, not only the ones that connected: a human working
    the same list would have spent time on the unanswered ones too, which
    is most of the saving. The per-call baselines are placeholders.
    """
    assumptions = {
        "minutes_per_connected_call": settings.manual_minutes_per_connected_call,
        "minutes_per_unanswered_call": settings.manual_minutes_per_unanswered_call,
        "status": "PLACEHOLDER baselines — time a human doing this to replace them",
    }
    if attempted <= 0:
        return Measure(
            name="Human calling hours saved",
            unit="hours",
            unavailable="no calls in this period yet",
            assumptions=assumptions,
        )

    minutes = (
        connected * settings.manual_minutes_per_connected_call
        + max(0, attempted - connected) * settings.manual_minutes_per_unanswered_call
    )
    return Measure(
        name="Human calling hours saved",
        value=round(minutes / 60, 1),
        unit="hours",
        sample=attempted,
        assumptions=assumptions,
    )


def _comparison(connected: int, total_seconds: int) -> dict:
    """MVP §38's "Current Human Process VS AI + Human Process".

    Deliberately thin. The honest comparison needs a measured baseline from
    Swaraj's own team, and inventing one would produce exactly the
    impressive-looking number §38 is warning against.
    """
    return {
        "ai_process": {
            "calls_handled_without_a_person": connected,
            "ai_minutes": round(total_seconds / 60, 1),
        },
        "human_process": None,
        "note": (
            "The baseline has to be measured from Swaraj's own team — how "
            "many leads an executive works in a day and how long a "
            "qualification call takes them. Until that exists this half is "
            "empty on purpose: a guessed baseline makes the comparison say "
            "whatever the guess said."
        ),
    }
