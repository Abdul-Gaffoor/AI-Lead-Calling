import datetime as dt
import enum

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base
from backend.telephony.base import CallState


class Disposition(str, enum.Enum):
    """Fixed disposition codes (MVP section 22)."""

    QUALIFIED_HOT = "QUALIFIED_HOT"
    QUALIFIED_WARM = "QUALIFIED_WARM"
    QUALIFIED_COLD = "QUALIFIED_COLD"

    SITE_SURVEY_REQUESTED = "SITE_SURVEY_REQUESTED"
    CALLBACK_REQUESTED = "CALLBACK_REQUESTED"

    HUMAN_TRANSFER = "HUMAN_TRANSFER"

    NO_ANSWER = "NO_ANSWER"
    BUSY = "BUSY"
    SWITCHED_OFF = "SWITCHED_OFF"

    NOT_INTERESTED = "NOT_INTERESTED"
    WRONG_NUMBER = "WRONG_NUMBER"

    EXISTING_CUSTOMER = "EXISTING_CUSTOMER"
    SERVICE_REQUEST = "SERVICE_REQUEST"

    DO_NOT_CALL = "DO_NOT_CALL"

    AI_ERROR = "AI_ERROR"
    TELEPHONY_ERROR = "TELEPHONY_ERROR"


#: Dispositions that end the lead's journey in a campaign — never retried.
TERMINAL_DISPOSITIONS = frozenset(
    {
        Disposition.QUALIFIED_HOT,
        Disposition.QUALIFIED_WARM,
        Disposition.QUALIFIED_COLD,
        Disposition.SITE_SURVEY_REQUESTED,
        Disposition.HUMAN_TRANSFER,
        Disposition.NOT_INTERESTED,
        Disposition.WRONG_NUMBER,
        Disposition.EXISTING_CUSTOMER,
        Disposition.SERVICE_REQUEST,
        Disposition.DO_NOT_CALL,
    }
)

#: Provider call states mapped onto the disposition they imply when no
#: richer outcome was recorded by the AI agent.
STATE_DISPOSITIONS = {
    CallState.NO_ANSWER: Disposition.NO_ANSWER,
    CallState.BUSY: Disposition.BUSY,
    CallState.FAILED: Disposition.TELEPHONY_ERROR,
}


class CallAttempt(Base):
    """A single outbound (or inbound) call (MVP sections 7, 21, 29)."""

    __tablename__ = "call_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaigns.id"), index=True, nullable=True
    )
    campaign_lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaign_leads.id"), index=True, nullable=True
    )
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"), index=True, nullable=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"), index=True, nullable=False
    )

    direction: Mapped[str] = mapped_column(String(10), default="OUTBOUND", nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_call_id: Mapped[str | None] = mapped_column(
        String(120), unique=True, index=True, nullable=True
    )
    to_number: Mapped[str] = mapped_column(String(20), nullable=False)
    from_number: Mapped[str | None] = mapped_column(String(20), nullable=True)

    state: Mapped[CallState] = mapped_column(
        Enum(CallState, name="call_state"), default=CallState.QUEUED, index=True, nullable=False
    )
    disposition: Mapped[Disposition | None] = mapped_column(
        Enum(Disposition, name="call_disposition"), index=True, nullable=True
    )

    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recording_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Structured data extracted by the AI agent (populated in Sprint 3+).
    ai_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    campaign_lead = relationship("CampaignLead")
    recording = relationship(
        "CallRecording", back_populates="call_attempt",
        cascade="all, delete-orphan", uselist=False,
    )


class CallRecording(Base):
    """A stored call recording (MVP section 29).

    The audio itself lives in object storage; this row says where, so that a
    retention policy can delete recordings without touching call history, and
    so nothing has to guess a storage key from call fields.
    """

    __tablename__ = "call_recordings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_attempt_id: Mapped[int] = mapped_column(
        ForeignKey("call_attempts.id"), unique=True, index=True, nullable=False
    )
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(80), default="audio/mpeg", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Where it came from, when the telephony provider supplied a URL.
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    call_attempt = relationship("CallAttempt", back_populates="recording")
