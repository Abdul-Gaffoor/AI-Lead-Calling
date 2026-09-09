import datetime as dt

from pydantic import BaseModel, ConfigDict

from backend.calls.models import Disposition
from backend.telephony.base import CallState


class CallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int | None
    campaign_lead_id: int | None
    lead_id: int | None
    customer_id: int
    direction: str
    attempt_number: int
    provider: str
    provider_call_id: str | None
    to_number: str
    from_number: str | None
    state: CallState
    disposition: Disposition | None
    duration_seconds: int | None
    recording_url: str | None
    summary: str | None
    ai_payload: dict | None
    started_at: dt.datetime
    ended_at: dt.datetime | None


class DispositionRequest(BaseModel):
    disposition: Disposition
    summary: str | None = None
    ai_payload: dict | None = None
    #: When the customer asked to be called back; overrides the retry rules.
    callback_at: dt.datetime | None = None


class WebhookAck(BaseModel):
    received: bool = True
    call_id: int | None = None
