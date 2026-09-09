"""Public website lead intake (MVP section 2B).

    swarajsolar.com -> Lead API -> Lead Management -> AI Campaign

Authenticated with a shared API key rather than a user session, since the
website posts these. Runs the same validation, deduplication and DNC checks
as an Excel upload — a website lead is never a way around the suppression
list.
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.compliance.service import is_suppressed, log_action
from backend.core.config import settings
from backend.core.database import get_db
from backend.customers.models import Customer
from backend.leads.models import Lead, LeadStatus, ServiceType
from backend.leads.phone import normalize_indian_mobile
from backend.solar_engine.calculator import estimate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public", tags=["public website"])


def require_website_key(x_api_key: str | None = Header(default=None)) -> None:
    if not settings.website_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Website lead intake is not configured",
        )
    if x_api_key != settings.website_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")


class WebsiteLead(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    #: Validated by normalize_indian_mobile, so every bad number gets the
    #: same structured INVALID_PHONE answer rather than a 422.
    mobile: str = Field(min_length=1, max_length=20)
    consent: bool = Field(description="Customer agreed to be contacted")
    service: ServiceType | None = None
    city: str | None = None
    district: str | None = None
    pincode: str | None = None
    monthly_bill: float | None = Field(default=None, ge=0)
    monthly_units: float | None = Field(default=None, ge=0)
    language: str | None = None
    remarks: str | None = None
    source: str = "WEBSITE"


class WebsiteLeadResult(BaseModel):
    accepted: bool
    lead_ref: str | None = None
    reason: str | None = None


@router.post(
    "/leads",
    response_model=WebsiteLeadResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_website_key)],
)
def website_lead(payload: WebsiteLead, db: Session = Depends(get_db)):
    """Accept a lead from the Swaraj Solar website."""
    phone = normalize_indian_mobile(payload.mobile)
    if phone is None:
        return WebsiteLeadResult(accepted=False, reason="INVALID_PHONE")
    if not payload.consent:
        return WebsiteLeadResult(accepted=False, reason="CONSENT_MISSING")

    customer = db.scalar(select(Customer).where(Customer.phone == phone))
    if (customer is not None and customer.opted_out) or is_suppressed(db, phone):
        return WebsiteLeadResult(accepted=False, reason="OPTED_OUT")

    if customer is None:
        customer = Customer(phone=phone, name=payload.name)
        db.add(customer)
        db.flush()

    open_lead = db.scalar(
        select(Lead).where(
            Lead.customer_id == customer.id,
            Lead.status.in_((LeadStatus.READY, LeadStatus.IN_CAMPAIGN)),
        )
    )
    if open_lead is not None:
        return WebsiteLeadResult(
            accepted=False, lead_ref=open_lead.lead_ref, reason="DUPLICATE_EXISTING"
        )

    lead = Lead(
        customer_id=customer.id,
        source=payload.source,
        consent_status="YES",
        consent_source="WEBSITE_FORM",
        interested_service=payload.service,
        language=payload.language,
        city=payload.city,
        district=payload.district,
        pincode=payload.pincode,
        monthly_bill=payload.monthly_bill,
        monthly_units=payload.monthly_units,
        remarks=payload.remarks,
        status=LeadStatus.READY,
    )
    db.add(lead)
    db.flush()
    lead.lead_ref = f"SW-{10000 + lead.id}"

    log_action(
        db, actor=None, action="WEBSITE_LEAD_RECEIVED", entity=f"lead:{lead.id}"
    )
    db.commit()
    return WebsiteLeadResult(accepted=True, lead_ref=lead.lead_ref)


class PublicEstimateRequest(BaseModel):
    monthly_bill: float | None = Field(default=None, ge=0)
    monthly_units: float | None = Field(default=None, ge=0)
    roof_area_sqft: float | None = Field(default=None, gt=0)
    subsidy_eligible: bool = True


@router.post("/solar-estimate", dependencies=[Depends(require_website_key)])
def public_estimate(payload: PublicEstimateRequest):
    """The website ROI calculator, using the same approved engine."""
    try:
        result = estimate(
            monthly_bill=payload.monthly_bill,
            monthly_units=payload.monthly_units,
            roof_area_sqft=payload.roof_area_sqft,
            subsidy_eligible=payload.subsidy_eligible,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    from dataclasses import asdict

    return asdict(result)
