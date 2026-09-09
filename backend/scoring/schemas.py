import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from backend.leads.models import ServiceType


class ScoringConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    service: ServiceType
    rules: list
    bands: list
    updated_at: dt.datetime


class ScoringConfigUpdate(BaseModel):
    rules: list[dict] | None = None
    bands: list[list] | None = None


class ScoreRequest(BaseModel):
    service: ServiceType
    collected: dict = Field(default_factory=dict)


class ScoreOut(BaseModel):
    score: int
    max_score: int
    classification: str
    matched: list[str]
    missed: list[str]
