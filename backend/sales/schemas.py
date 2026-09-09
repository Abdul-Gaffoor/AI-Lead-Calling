import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from backend.leads.models import ServiceType
from backend.sales.models import OpportunityStage


class OpportunityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    customer_id: int
    service: ServiceType | None
    stage: OpportunityStage
    score: int | None
    classification: str | None
    summary: str | None
    assigned_to_id: int | None
    next_follow_up_at: dt.datetime | None
    quotation_amount: Decimal | None
    lost_reason: str | None
    created_at: dt.datetime


class PriorityLead(OpportunityOut):
    """What the executive actually sees in their queue (MVP section 26)."""

    customer_name: str
    customer_phone: str
    city: str | None = None
    monthly_bill: Decimal | None = None


class OpportunityUpdate(BaseModel):
    stage: OpportunityStage | None = None
    assigned_to_id: int | None = None
    next_follow_up_at: dt.datetime | None = None
    quotation_amount: float | None = Field(default=None, ge=0)
    lost_reason: str | None = None


class NoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    opportunity_id: int
    author_id: int
    body: str
    created_at: dt.datetime
