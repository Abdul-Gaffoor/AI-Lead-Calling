import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.knowledge.models import CATEGORIES
from backend.leads.models import ServiceType


class DocumentIn(BaseModel):
    slug: str = Field(min_length=2, max_length=120)
    title: str = Field(min_length=2, max_length=200)
    category: str
    body: str = Field(min_length=1)
    service: ServiceType | None = None
    source: str | None = Field(default=None, max_length=300)
    language: str = Field(default="en", max_length=10)

    @field_validator("category")
    @classmethod
    def known_category(cls, value: str) -> str:
        if value not in CATEGORIES:
            raise ValueError(f"category must be one of: {', '.join(CATEGORIES)}")
        return value


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    title: str
    category: str
    service: ServiceType | None
    source: str | None
    language: str
    is_approved: bool
    approved_at: dt.datetime | None
    created_at: dt.datetime


class DocumentDetail(DocumentOut):
    body: str
    passages: int = 0


class PassageOut(BaseModel):
    chunk_id: int
    document_id: int
    slug: str
    title: str
    category: str
    text: str
    similarity: float
    source: str | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    service: ServiceType | None = None
    category: str | None = None
    limit: int | None = Field(default=None, ge=1, le=20)


class ReindexResult(BaseModel):
    documents: int
    reindexed: int
    passages: int
    model: str
