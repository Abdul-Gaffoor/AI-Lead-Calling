from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.auth.dependencies import get_current_user, require_roles
from backend.auth.models import Role, User
from backend.core.database import get_db
from backend.customers.models import Customer
from backend.leads.models import Lead
from backend.sales.models import Opportunity, OpportunityNote, OpportunityStage
from backend.sales.schemas import (
    NoteCreate,
    NoteOut,
    OpportunityOut,
    OpportunityUpdate,
    PriorityLead,
)

router = APIRouter(prefix="/sales", tags=["sales"])

#: Order HOT first, then by score — an executive's queue, not a raw list.
_CLASS_ORDER = {"HOT": 0, "WARM": 1, "COLD": 2, "UNQUALIFIED": 3}


@router.get("/my-leads", response_model=list[PriorityLead])
def my_priority_leads(
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The executive's own queue, highest-value first (MVP section 26)."""
    rows = db.execute(
        select(Opportunity, Customer, Lead)
        .join(Customer, Customer.id == Opportunity.customer_id)
        .join(Lead, Lead.id == Opportunity.lead_id)
        .where(
            Opportunity.assigned_to_id == current_user.id,
            Opportunity.stage.notin_((OpportunityStage.WON, OpportunityStage.LOST)),
        )
    ).all()

    rows.sort(
        key=lambda row: (
            _CLASS_ORDER.get(row[0].classification or "UNQUALIFIED", 9),
            -(row[0].score or 0),
        )
    )
    return [
        PriorityLead(
            **OpportunityOut.model_validate(opportunity, from_attributes=True).model_dump(),
            customer_name=customer.name,
            customer_phone=customer.phone,
            city=lead.city,
            monthly_bill=lead.monthly_bill,
        )
        for opportunity, customer, lead in rows[:limit]
    ]


@router.get("/opportunities", response_model=list[OpportunityOut])
def list_opportunities(
    stage: OpportunityStage | None = None,
    classification: str | None = None,
    assigned_to_id: int | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Opportunity).order_by(Opportunity.score.desc().nulls_last(), Opportunity.id.desc())
    if stage is not None:
        stmt = stmt.where(Opportunity.stage == stage)
    if classification is not None:
        stmt = stmt.where(Opportunity.classification == classification.upper())
    if assigned_to_id is not None:
        stmt = stmt.where(Opportunity.assigned_to_id == assigned_to_id)
    return db.scalars(stmt.limit(limit).offset(offset)).all()


@router.get("/opportunities/{opportunity_id}", response_model=OpportunityOut)
def get_opportunity(
    opportunity_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    opportunity = db.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunity not found")
    return opportunity


@router.patch("/opportunities/{opportunity_id}", response_model=OpportunityOut)
def update_opportunity(
    opportunity_id: int,
    payload: OpportunityUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    opportunity = db.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunity not found")

    changes = payload.model_dump(exclude_unset=True)
    # Only managers may reassign someone else's opportunity.
    if (
        "assigned_to_id" in changes
        and opportunity.assigned_to_id not in (None, current_user.id)
        and current_user.role not in (Role.SUPER_ADMIN, Role.SALES_MANAGER)
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only a manager can reassign another executive's lead"
        )

    for field, value in changes.items():
        setattr(opportunity, field, value)
    db.commit()
    db.refresh(opportunity)
    return opportunity


@router.post(
    "/opportunities/{opportunity_id}/notes",
    response_model=NoteOut,
    status_code=status.HTTP_201_CREATED,
)
def add_note(
    opportunity_id: int,
    payload: NoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if db.get(Opportunity, opportunity_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opportunity not found")
    note = OpportunityNote(
        opportunity_id=opportunity_id, author_id=current_user.id, body=payload.body
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.get("/opportunities/{opportunity_id}/notes", response_model=list[NoteOut])
def list_notes(
    opportunity_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return db.scalars(
        select(OpportunityNote)
        .where(OpportunityNote.opportunity_id == opportunity_id)
        .order_by(OpportunityNote.id.desc())
    ).all()
