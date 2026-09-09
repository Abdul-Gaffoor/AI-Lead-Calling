"""Turning qualified conversations into sales work (MVP sections 25, 26)."""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.auth.models import Role, User
from backend.calls.models import CallAttempt, Disposition
from backend.leads.models import Lead
from backend.sales.models import Opportunity, OpportunityStage
from backend.surveys.models import SiteSurvey, SurveyStatus

logger = logging.getLogger(__name__)

#: Dispositions that mean sales should pick the lead up.
SALES_DISPOSITIONS = {
    Disposition.QUALIFIED_HOT,
    Disposition.QUALIFIED_WARM,
    Disposition.QUALIFIED_COLD,
    Disposition.SITE_SURVEY_REQUESTED,
    Disposition.HUMAN_TRANSFER,
}


def assign_least_loaded(db: Session, *, role: Role = Role.SALES_EXECUTIVE) -> User | None:
    """Pick the active executive with the fewest open opportunities."""
    executives = db.scalars(
        select(User).where(User.role == role, User.is_active.is_(True))
    ).all()
    if not executives:
        return None

    open_counts = dict(
        db.execute(
            select(Opportunity.assigned_to_id, func.count(Opportunity.id))
            .where(
                Opportunity.stage.notin_((OpportunityStage.WON, OpportunityStage.LOST)),
                Opportunity.assigned_to_id.isnot(None),
            )
            .group_by(Opportunity.assigned_to_id)
        ).all()
    )
    return min(executives, key=lambda user: (open_counts.get(user.id, 0), user.id))


def create_from_call(
    db: Session,
    attempt: CallAttempt,
    *,
    score: int | None = None,
    classification: str | None = None,
) -> Opportunity | None:
    """Create (or update) the sales opportunity for a finished call.

    Also books a site survey when the customer asked for one, so nothing
    depends on an executive remembering to create it.
    """
    if attempt.disposition not in SALES_DISPOSITIONS or attempt.lead_id is None:
        return None

    opportunity = db.scalar(
        select(Opportunity).where(Opportunity.lead_id == attempt.lead_id)
    )
    lead = db.get(Lead, attempt.lead_id)
    if lead is None:
        return None

    if opportunity is None:
        opportunity = Opportunity(
            lead_id=lead.id,
            customer_id=attempt.customer_id,
            service=lead.interested_service,
            stage=OpportunityStage.NEW,
        )
        db.add(opportunity)

    opportunity.score = score
    opportunity.classification = classification
    opportunity.summary = attempt.summary
    if attempt.ai_payload and attempt.ai_payload.get("service"):
        try:
            from backend.leads.models import ServiceType

            opportunity.service = ServiceType(attempt.ai_payload["service"])
        except ValueError:
            pass

    if opportunity.assigned_to_id is None:
        # Big industrial and commercial projects go to a senior executive
        # rather than round-robin (MVP section 15).
        executive = assign_least_loaded(db)
        opportunity.assigned_to_id = executive.id if executive else None

    if attempt.disposition is Disposition.SITE_SURVEY_REQUESTED:
        opportunity.stage = OpportunityStage.SURVEY
        _ensure_survey(db, opportunity, lead)

    db.flush()
    return opportunity


def _ensure_survey(db: Session, opportunity: Opportunity, lead: Lead) -> SiteSurvey:
    existing = db.scalar(
        select(SiteSurvey).where(
            SiteSurvey.lead_id == lead.id,
            SiteSurvey.status.notin_((SurveyStatus.CANCELLED, SurveyStatus.COMPLETED)),
        )
    )
    if existing is not None:
        return existing
    survey = SiteSurvey(
        lead_id=lead.id,
        customer_id=lead.customer_id,
        service=opportunity.service,
        status=SurveyStatus.REQUESTED,
        address=", ".join(filter(None, [lead.city, lead.district, lead.pincode])) or None,
    )
    db.add(survey)
    return survey
