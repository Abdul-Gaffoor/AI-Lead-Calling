import datetime as dt

from pydantic import BaseModel, ConfigDict

from backend.leads.models import ServiceType
from backend.surveys.models import SurveyStatus


class SurveyCreate(BaseModel):
    lead_id: int | None = None
    customer_id: int | None = None
    service: ServiceType | None = None
    address: str | None = None
    preferred_date: dt.date | None = None
    preferred_time: str | None = None
    notes: str | None = None


class SurveyUpdate(BaseModel):
    status: SurveyStatus | None = None
    address: str | None = None
    preferred_date: dt.date | None = None
    preferred_time: str | None = None
    scheduled_at: dt.datetime | None = None
    assigned_to_id: int | None = None
    notes: str | None = None
    gps_latitude: float | None = None
    gps_longitude: float | None = None


class SurveyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int | None
    customer_id: int
    service: ServiceType | None
    status: SurveyStatus
    address: str | None
    preferred_date: dt.date | None
    preferred_time: str | None
    scheduled_at: dt.datetime | None
    assigned_to_id: int | None
    notes: str | None
    created_at: dt.datetime
