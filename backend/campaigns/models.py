import datetime as dt
import enum

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base
from backend.leads.models import ServiceType


class CampaignStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"


class CampaignLeadStatus(str, enum.Enum):
    PENDING = "PENDING"  # waiting for its next attempt
    DIALING = "DIALING"  # a call is in flight
    COMPLETED = "COMPLETED"  # reached a terminal disposition
    EXHAUSTED = "EXHAUSTED"  # max attempts used without contact
    SUPPRESSED = "SUPPRESSED"  # opted out after being queued
    CANCELLED = "CANCELLED"  # removed with the campaign


#: Default minutes to wait before retrying, per disposition (MVP section 7).
DEFAULT_RETRY_MINUTES: dict[str, int] = {
    "NO_ANSWER": 120,
    "BUSY": 30,
    "SWITCHED_OFF": 240,
    "TELEPHONY_ERROR": 15,
    "AI_ERROR": 15,
}


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus, name="campaign_status"),
        default=CampaignStatus.DRAFT,
        index=True,
        nullable=False,
    )

    service: Mapped[ServiceType | None] = mapped_column(
        Enum(ServiceType, name="service_type"), nullable=True
    )
    language: Mapped[str | None] = mapped_column(String(30), nullable=True)

    start_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    # Calling window, in the campaign timezone (MVP section 6)
    window_start: Mapped[dt.time] = mapped_column(
        Time, default=dt.time(10, 0), nullable=False
    )
    window_end: Mapped[dt.time] = mapped_column(
        Time, default=dt.time(18, 0), nullable=False
    )
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Kolkata", nullable=False)

    concurrency: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Per-disposition retry delays in minutes; overrides DEFAULT_RETRY_MINUTES.
    retry_rules: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    entries = relationship("CampaignLead", back_populates="campaign")

    def retry_delay_minutes(self, disposition: str) -> int | None:
        """Minutes to wait before the next attempt, or None if not retryable."""
        rules = {**DEFAULT_RETRY_MINUTES, **(self.retry_rules or {})}
        value = rules.get(disposition)
        return int(value) if value is not None else None


class CampaignLead(Base):
    """One lead's place in a campaign queue, with its attempt state."""

    __tablename__ = "campaign_leads"
    __table_args__ = (UniqueConstraint("campaign_id", "lead_id", name="uq_campaign_lead"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id"), index=True, nullable=False
    )
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True, nullable=False)

    status: Mapped[CampaignLeadStatus] = mapped_column(
        Enum(CampaignLeadStatus, name="campaign_lead_status"),
        default=CampaignLeadStatus.PENDING,
        index=True,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    #: Set when the customer asked to be called at a specific time; this takes
    #: precedence over the automatic retry rules (MVP section 7).
    customer_requested_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_disposition: Mapped[str | None] = mapped_column(String(40), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    campaign = relationship("Campaign", back_populates="entries")
    lead = relationship("Lead")
