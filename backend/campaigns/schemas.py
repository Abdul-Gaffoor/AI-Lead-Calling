import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.campaigns.models import CampaignLeadStatus, CampaignStatus
from backend.leads.models import ServiceType


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    service: ServiceType | None = None
    language: str | None = Field(default=None, max_length=30)
    start_date: dt.date | None = None
    window_start: dt.time = dt.time(10, 0)
    window_end: dt.time = dt.time(18, 0)
    timezone: str = "Asia/Kolkata"
    concurrency: int = Field(default=5, ge=1, le=200)
    max_attempts: int = Field(default=3, ge=1, le=10)
    priority: int = Field(default=0, ge=0, le=100)
    #: Per-disposition retry delays in minutes, e.g. {"NO_ANSWER": 120}
    retry_rules: dict[str, int] | None = None


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    window_start: dt.time | None = None
    window_end: dt.time | None = None
    concurrency: int | None = Field(default=None, ge=1, le=200)
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    priority: int | None = Field(default=None, ge=0, le=100)
    retry_rules: dict[str, int] | None = None


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    status: CampaignStatus
    service: ServiceType | None
    language: str | None
    start_date: dt.date | None
    window_start: dt.time
    window_end: dt.time
    timezone: str
    concurrency: int
    max_attempts: int
    priority: int
    retry_rules: dict | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    completed_at: dt.datetime | None


class CampaignDetail(CampaignOut):
    stats: dict


class AddLeadsRequest(BaseModel):
    lead_ids: list[int] | None = None
    upload_id: int | None = None

    @model_validator(mode="after")
    def check_one_source(self):
        if bool(self.lead_ids) == (self.upload_id is not None):
            raise ValueError("Provide exactly one of lead_ids or upload_id")
        return self


class AddLeadsResult(BaseModel):
    added: int
    skipped: int
    suppressed: int


class CampaignLeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    lead_id: int
    status: CampaignLeadStatus
    attempts: int
    next_attempt_at: dt.datetime | None
    customer_requested_at: dt.datetime | None
    last_disposition: str | None


class DispatchResult(BaseModel):
    calls_placed: int
