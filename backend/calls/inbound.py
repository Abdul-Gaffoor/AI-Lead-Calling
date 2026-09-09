"""Inbound callback handling (MVP section 24).

When a customer rings the business number back, we identify them by number
and retrieve their previous context so the conversation continues instead of
starting over.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.calls.models import CallAttempt
from backend.customers.models import Customer
from backend.leads.models import Lead, LeadStatus
from backend.sales.models import Opportunity, OpportunityStage
from backend.telephony.base import CallState


def identify_caller(db: Session, phone: str) -> dict:
    """Everything known about an inbound caller."""
    customer = db.scalar(select(Customer).where(Customer.phone == phone))
    if customer is None:
        return {"known": False, "customer": None}

    last_call = db.scalar(
        select(CallAttempt)
        .where(CallAttempt.customer_id == customer.id)
        .order_by(CallAttempt.id.desc())
        .limit(1)
    )
    open_lead = db.scalar(
        select(Lead)
        .where(
            Lead.customer_id == customer.id,
            Lead.status.in_((LeadStatus.READY, LeadStatus.IN_CAMPAIGN)),
        )
        .order_by(Lead.id.desc())
        .limit(1)
    )
    lead = open_lead or db.scalar(
        select(Lead).where(Lead.customer_id == customer.id).order_by(Lead.id.desc()).limit(1)
    )
    opportunity = db.scalar(
        select(Opportunity)
        .where(
            Opportunity.customer_id == customer.id,
            Opportunity.stage.notin_((OpportunityStage.WON, OpportunityStage.LOST)),
        )
        .order_by(Opportunity.id.desc())
        .limit(1)
    )

    return {
        "known": True,
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "phone": customer.phone,
            "opted_out": customer.opted_out,
            "is_existing_customer": customer.is_existing_customer,
        },
        "lead": (
            {
                "id": lead.id,
                "lead_ref": lead.lead_ref,
                "service": lead.interested_service.value if lead.interested_service else None,
                "city": lead.city,
                "monthly_bill": float(lead.monthly_bill) if lead.monthly_bill else None,
            }
            if lead
            else None
        ),
        "last_call": (
            {
                "id": last_call.id,
                "disposition": last_call.disposition.value if last_call.disposition else None,
                "summary": last_call.summary,
                "started_at": last_call.started_at.isoformat() if last_call.started_at else None,
            }
            if last_call
            else None
        ),
        "opportunity": (
            {
                "id": opportunity.id,
                "stage": opportunity.stage.value,
                "classification": opportunity.classification,
                "assigned_to_id": opportunity.assigned_to_id,
            }
            if opportunity
            else None
        ),
    }


def register_inbound_call(
    db: Session, *, phone: str, provider: str, provider_call_id: str | None = None
) -> tuple[CallAttempt, dict]:
    """Record an inbound call and return it with the caller's context."""
    context = identify_caller(db, phone)

    customer = db.scalar(select(Customer).where(Customer.phone == phone))
    if customer is None:
        # An unknown caller is still a customer — create the record so the
        # conversation and any follow-up have something to attach to.
        customer = Customer(phone=phone, name="Unknown caller")
        db.add(customer)
        db.flush()
        context["customer"] = {
            "id": customer.id,
            "name": customer.name,
            "phone": customer.phone,
            "opted_out": False,
            "is_existing_customer": False,
        }

    lead_id = context.get("lead", {}).get("id") if context.get("lead") else None
    attempt = CallAttempt(
        customer_id=customer.id,
        lead_id=lead_id,
        direction="INBOUND",
        attempt_number=1,
        provider=provider,
        provider_call_id=provider_call_id,
        to_number=phone,
        state=CallState.IN_PROGRESS,
    )
    db.add(attempt)
    db.flush()
    return attempt, context
