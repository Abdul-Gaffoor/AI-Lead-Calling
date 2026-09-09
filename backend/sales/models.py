import datetime as dt
import enum

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base
from backend.leads.models import ServiceType


class OpportunityStage(str, enum.Enum):
    """Sales funnel beyond qualification (MVP sections 26 and 28)."""

    NEW = "NEW"
    CONTACTED = "CONTACTED"
    SURVEY = "SURVEY"
    QUOTATION = "QUOTATION"
    WON = "WON"
    LOST = "LOST"


class Opportunity(Base):
    """A qualified lead being worked by a sales executive."""

    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id"), unique=True, index=True, nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"), index=True, nullable=False
    )
    service: Mapped[ServiceType | None] = mapped_column(
        Enum(ServiceType, name="service_type"), nullable=True
    )

    stage: Mapped[OpportunityStage] = mapped_column(
        Enum(OpportunityStage, name="opportunity_stage"),
        default=OpportunityStage.NEW,
        index=True,
        nullable=False,
    )
    score: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    classification: Mapped[str | None] = mapped_column(String(20), index=True, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    assigned_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=True
    )
    next_follow_up_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    quotation_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    lost_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class OpportunityNote(Base):
    __tablename__ = "opportunity_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id"), index=True, nullable=False
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
