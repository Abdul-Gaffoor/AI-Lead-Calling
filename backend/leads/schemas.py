import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from backend.leads.models import LeadStatus, RejectionReason, ServiceType, UploadStatus


class UploadSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    status: UploadStatus
    error: str | None
    total_rows: int
    valid_count: int
    duplicate_count: int
    invalid_phone_count: int
    opted_out_count: int
    consent_issue_count: int
    missing_field_count: int
    created_at: dt.datetime


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_ref: str | None
    customer_id: int
    upload_id: int | None
    source: str
    status: LeadStatus
    consent_status: str
    consent_date: str | None
    consent_source: str | None
    interested_service: ServiceType | None
    language: str | None
    alternate_number: str | None
    city: str | None
    district: str | None
    mandal: str | None
    pincode: str | None
    monthly_bill: Decimal | None
    monthly_units: Decimal | None
    campaign_hint: str | None
    remarks: str | None
    created_at: dt.datetime


class LeadWithCustomer(LeadOut):
    customer_name: str
    customer_phone: str


class RejectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    row_number: int
    customer_name: str | None
    mobile_number: str | None
    reason: RejectionReason
    details: str | None
