"""Campaign lifecycle and queue management (MVP section 6)."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.auth.models import User
from backend.calls.models import CallAttempt, Disposition
from backend.calls.service import utcnow
from backend.campaigns.models import Campaign, CampaignLead, CampaignLeadStatus, CampaignStatus
from backend.compliance.service import is_suppressed, log_action
from backend.customers.models import Customer
from backend.leads.models import Lead, LeadStatus

#: Allowed campaign state transitions. STOPPED and COMPLETED are final.
TRANSITIONS: dict[CampaignStatus, set[CampaignStatus]] = {
    CampaignStatus.DRAFT: {CampaignStatus.RUNNING, CampaignStatus.STOPPED},
    CampaignStatus.RUNNING: {
        CampaignStatus.PAUSED,
        CampaignStatus.STOPPED,
        CampaignStatus.COMPLETED,
    },
    CampaignStatus.PAUSED: {CampaignStatus.RUNNING, CampaignStatus.STOPPED},
    CampaignStatus.STOPPED: set(),
    CampaignStatus.COMPLETED: set(),
}


class CampaignStateError(Exception):
    """Raised for a transition the campaign lifecycle does not allow."""


def transition(
    db: Session, campaign: Campaign, target: CampaignStatus, *, actor: User | None = None
) -> Campaign:
    if target not in TRANSITIONS[campaign.status]:
        raise CampaignStateError(
            f"Cannot move campaign from {campaign.status.value} to {target.value}"
        )

    campaign.status = target
    if target is CampaignStatus.RUNNING and campaign.started_at is None:
        campaign.started_at = utcnow()
    if target in (CampaignStatus.COMPLETED, CampaignStatus.STOPPED):
        campaign.completed_at = utcnow()
    if target is CampaignStatus.STOPPED:
        # Release queued leads so they can be added to another campaign.
        for entry in db.scalars(
            select(CampaignLead).where(
                CampaignLead.campaign_id == campaign.id,
                CampaignLead.status == CampaignLeadStatus.PENDING,
            )
        ):
            entry.status = CampaignLeadStatus.CANCELLED
            lead = db.get(Lead, entry.lead_id)
            if lead is not None and lead.status is LeadStatus.IN_CAMPAIGN:
                lead.status = LeadStatus.READY

    log_action(
        db,
        actor=actor,
        action=f"CAMPAIGN_{target.value}",
        entity=f"campaign:{campaign.id}",
    )
    return campaign


def add_leads(
    db: Session,
    campaign: Campaign,
    *,
    lead_ids: list[int] | None = None,
    upload_id: int | None = None,
    actor: User | None = None,
) -> dict[str, int]:
    """Queue leads onto a campaign.

    Only READY leads are eligible; suppressed numbers and leads already in
    this campaign are skipped. Returns a summary of what happened.
    """
    stmt = select(Lead).where(Lead.status == LeadStatus.READY)
    if lead_ids:
        stmt = stmt.where(Lead.id.in_(lead_ids))
    elif upload_id is not None:
        stmt = stmt.where(Lead.upload_id == upload_id)
    else:
        raise ValueError("Provide either lead_ids or upload_id")

    if campaign.service is not None:
        stmt = stmt.where(
            (Lead.interested_service == campaign.service) | (Lead.interested_service.is_(None))
        )

    existing = {
        row
        for row in db.scalars(
            select(CampaignLead.lead_id).where(CampaignLead.campaign_id == campaign.id)
        )
    }

    added = skipped = suppressed = 0
    now = utcnow()
    for lead in db.scalars(stmt):
        if lead.id in existing:
            skipped += 1
            continue
        customer = db.get(Customer, lead.customer_id)
        if customer is None or customer.opted_out or is_suppressed(db, customer.phone):
            suppressed += 1
            continue
        db.add(
            CampaignLead(
                campaign_id=campaign.id,
                lead_id=lead.id,
                status=CampaignLeadStatus.PENDING,
                next_attempt_at=now,
            )
        )
        lead.status = LeadStatus.IN_CAMPAIGN
        added += 1

    log_action(
        db,
        actor=actor,
        action="CAMPAIGN_LEADS_ADDED",
        entity=f"campaign:{campaign.id}",
        details={"added": added, "skipped": skipped, "suppressed": suppressed},
    )
    return {"added": added, "skipped": skipped, "suppressed": suppressed}


def campaign_stats(db: Session, campaign_id: int) -> dict:
    """Queue and call counters for the campaign (feeds the dashboard)."""
    queue_rows = db.execute(
        select(CampaignLead.status, func.count(CampaignLead.id))
        .where(CampaignLead.campaign_id == campaign_id)
        .group_by(CampaignLead.status)
    ).all()
    disposition_rows = db.execute(
        select(CallAttempt.disposition, func.count(CallAttempt.id))
        .where(CallAttempt.campaign_id == campaign_id)
        .group_by(CallAttempt.disposition)
    ).all()

    dispositions = {
        (key.value if isinstance(key, Disposition) else str(key)): count
        for key, count in disposition_rows
        if key is not None
    }
    total_calls = (
        db.scalar(
            select(func.count(CallAttempt.id)).where(CallAttempt.campaign_id == campaign_id)
        )
        or 0
    )
    connected = sum(
        count
        for code, count in dispositions.items()
        if code not in {"NO_ANSWER", "BUSY", "SWITCHED_OFF", "TELEPHONY_ERROR"}
    )
    return {
        "queue": {
            (key.value if isinstance(key, CampaignLeadStatus) else str(key)): count
            for key, count in queue_rows
        },
        "dispositions": dispositions,
        "total_calls": total_calls,
        "connected_calls": connected,
    }
