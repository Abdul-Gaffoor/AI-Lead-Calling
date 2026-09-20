import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from backend.calls.models import Disposition
from backend.leads.models import ServiceType
from backend.quality.models import ReviewFlag, ReviewVerdict


class QueueItem(BaseModel):
    call_id: int
    customer_name: str | None
    phone: str | None
    started_at: dt.datetime
    duration_seconds: int | None
    disposition: Disposition | None
    lead_score: int | None = None
    classification: str | None = None
    has_recording: bool
    review_count: int


class TurnOut(BaseModel):
    index: int
    speaker: str
    text: str
    language: str | None
    latency_ms: int | None


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reviewer_id: int
    verdict: ReviewVerdict
    flags: list[str]
    note: str | None
    created_at: dt.datetime


class CallReviewDetail(BaseModel):
    call_id: int
    customer_name: str | None
    phone: str | None
    city: str | None
    started_at: dt.datetime
    ended_at: dt.datetime | None
    duration_seconds: int | None
    direction: str
    disposition: Disposition | None
    language: str | None
    service: ServiceType | None
    transcript: list[TurnOut]
    extracted: dict
    summary: str | None
    lead_score: int | None
    classification: str | None
    ai_payload: dict
    has_recording: bool
    recording_seconds: int | None
    reviews: list[ReviewOut]


class ReviewIn(BaseModel):
    verdict: ReviewVerdict
    #: Required when the verdict is INCORRECT — an unexplained "wrong" cannot
    #: be counted, and counting is why these are collected.
    flags: list[ReviewFlag] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=2000)


class RecordingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    call_attempt_id: int
    content_type: str
    size_bytes: int
    duration_seconds: int | None
    created_at: dt.datetime


class QualitySummary(BaseModel):
    reviews: int
    reviewed_calls: int
    reviewable_calls: int
    coverage_percent: float
    correct: int
    incorrect: int
    accuracy_percent: float
    flags: dict[str, int]
