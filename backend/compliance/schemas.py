import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class OptOutCreate(BaseModel):
    phone: str = Field(min_length=8, max_length=20)
    reason: str = "OPT_OUT"
    source: str | None = None


class SuppressionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    phone: str
    reason: str
    source: str | None
    created_at: dt.datetime
