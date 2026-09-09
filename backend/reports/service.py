"""Manager dashboard and funnel reporting (MVP sections 27 and 28)."""

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.calls.models import CallAttempt, Disposition
from backend.leads.models import Lead, LeadUpload
from backend.sales.models import Opportunity, OpportunityStage
from backend.surveys.models import SiteSurvey, SurveyStatus
from backend.telephony.base import CallState

#: Dispositions that mean the customer actually spoke to the AI.
_CONNECTED = {
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
    Disposition.WRONG_NUMBER,
}

_QUALIFIED = {
    Disposition.QUALIFIED_HOT,
    Disposition.QUALIFIED_WARM,
    Disposition.QUALIFIED_COLD,
    Disposition.SITE_SURVEY_REQUESTED,
}


def _day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)
    return start, start + dt.timedelta(days=1)


def daily_dashboard(db: Session, day: dt.date | None = None) -> dict:
    """The manager's "today" view (MVP section 27)."""
    day = day or dt.datetime.now(dt.timezone.utc).date()
    start, end = _day_bounds(day)

    uploads = db.execute(
        select(
            func.coalesce(func.sum(LeadUpload.total_rows), 0),
            func.coalesce(func.sum(LeadUpload.valid_count), 0),
        ).where(LeadUpload.created_at >= start, LeadUpload.created_at < end)
    ).one()

    calls = db.scalars(
        select(CallAttempt).where(CallAttempt.started_at >= start, CallAttempt.started_at < end)
    ).all()

    dispositions: dict[str, int] = {}
    for call in calls:
        if call.disposition is not None:
            dispositions[call.disposition.value] = dispositions.get(call.disposition.value, 0) + 1

    connected = sum(1 for c in calls if c.disposition in _CONNECTED)
    qualified = sum(1 for c in calls if c.disposition in _QUALIFIED)

    by_service = dict(
        db.execute(
            select(Lead.interested_service, func.count(Lead.id))
            .where(Lead.created_at >= start, Lead.created_at < end)
            .group_by(Lead.interested_service)
        ).all()
    )

    classifications = dict(
        db.execute(
            select(Opportunity.classification, func.count(Opportunity.id))
            .where(Opportunity.created_at >= start, Opportunity.created_at < end)
            .group_by(Opportunity.classification)
        ).all()
    )

    surveys_today = db.scalar(
        select(func.count(SiteSurvey.id)).where(
            SiteSurvey.created_at >= start, SiteSurvey.created_at < end
        )
    )

    return {
        "date": day.isoformat(),
        "uploaded": int(uploads[0] or 0),
        "eligible": int(uploads[1] or 0),
        "attempted": len(calls),
        "connected": connected,
        "qualified": qualified,
        "hot": classifications.get("HOT", 0),
        "warm": classifications.get("WARM", 0),
        "cold": classifications.get("COLD", 0),
        "site_surveys": surveys_today or 0,
        "human_transfers": dispositions.get(Disposition.HUMAN_TRANSFER.value, 0),
        "callbacks": dispositions.get(Disposition.CALLBACK_REQUESTED.value, 0),
        "opt_outs": dispositions.get(Disposition.DO_NOT_CALL.value, 0),
        "dispositions": dispositions,
        "by_service": {
            (service.value if service is not None else "UNSPECIFIED"): count
            for service, count in by_service.items()
        },
    }


def funnel(db: Session, since: dt.date | None = None) -> dict:
    """LEAD → CALLED → CONNECTED → QUALIFIED → SURVEY → QUOTATION → ORDER."""
    filters = []
    if since is not None:
        start = dt.datetime.combine(since, dt.time.min, tzinfo=dt.timezone.utc)
        filters.append(start)

    def _count(model, *conditions):
        stmt = select(func.count(model.id))
        if filters:
            stmt = stmt.where(model.created_at >= filters[0])
        for condition in conditions:
            stmt = stmt.where(condition)
        return db.scalar(stmt) or 0

    leads = _count(Lead)
    called_stmt = select(func.count(func.distinct(CallAttempt.lead_id)))
    if filters:
        called_stmt = called_stmt.where(CallAttempt.started_at >= filters[0])
    called = db.scalar(called_stmt) or 0

    connected_stmt = select(func.count(func.distinct(CallAttempt.lead_id))).where(
        CallAttempt.disposition.in_(_CONNECTED)
    )
    qualified_stmt = select(func.count(func.distinct(CallAttempt.lead_id))).where(
        CallAttempt.disposition.in_(_QUALIFIED)
    )
    if filters:
        connected_stmt = connected_stmt.where(CallAttempt.started_at >= filters[0])
        qualified_stmt = qualified_stmt.where(CallAttempt.started_at >= filters[0])

    connected = db.scalar(connected_stmt) or 0
    qualified = db.scalar(qualified_stmt) or 0

    surveys = _count(SiteSurvey)
    quotations = _count(Opportunity, Opportunity.stage == OpportunityStage.QUOTATION)
    orders = _count(Opportunity, Opportunity.stage == OpportunityStage.WON)
    completed_surveys = _count(SiteSurvey, SiteSurvey.status == SurveyStatus.COMPLETED)

    def rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator * 100, 1) if denominator else 0.0

    return {
        "stages": {
            "lead": leads,
            "called": called,
            "connected": connected,
            "qualified": qualified,
            "site_survey": surveys,
            "survey_completed": completed_surveys,
            "quotation": quotations,
            "order": orders,
        },
        "rates": {
            "connection_rate": rate(connected, called),
            "qualification_rate": rate(qualified, connected),
            "site_survey_conversion": rate(surveys, qualified),
            "quotation_conversion": rate(quotations, surveys),
            "order_conversion": rate(orders, quotations),
        },
    }


def call_metrics(db: Session) -> dict:
    """Call-quality KPIs (MVP section 28)."""
    total = db.scalar(select(func.count(CallAttempt.id))) or 0
    answered = db.scalar(
        select(func.count(CallAttempt.id)).where(CallAttempt.duration_seconds.isnot(None))
    ) or 0
    total_seconds = db.scalar(
        select(func.coalesce(func.sum(CallAttempt.duration_seconds), 0))
    ) or 0
    in_flight = db.scalar(
        select(func.count(CallAttempt.id)).where(
            CallAttempt.state.in_((CallState.QUEUED, CallState.RINGING, CallState.IN_PROGRESS))
        )
    ) or 0

    return {
        "total_calls": total,
        "calls_with_duration": answered,
        "total_minutes": round(total_seconds / 60, 1),
        "average_call_seconds": round(total_seconds / answered, 1) if answered else 0.0,
        "in_flight": in_flight,
    }
