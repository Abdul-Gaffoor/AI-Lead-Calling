import datetime as dt
import enum

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base
from backend.leads.models import ServiceType


class ConversationState(str, enum.Enum):
    GREETING = "GREETING"  # disclosure delivered, awaiting first reply
    QUALIFYING = "QUALIFYING"
    ENDED = "ENDED"


class Speaker(str, enum.Enum):
    AI = "AI"
    CUSTOMER = "CUSTOMER"


class Conversation(Base):
    """One AI conversation attached to a call (MVP sections 9 and 21)."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_attempt_id: Mapped[int] = mapped_column(
        ForeignKey("call_attempts.id"), unique=True, index=True, nullable=False
    )
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"), index=True, nullable=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"), index=True, nullable=False
    )

    state: Mapped[ConversationState] = mapped_column(
        Enum(ConversationState, name="conversation_state"),
        default=ConversationState.GREETING,
        nullable=False,
    )
    language: Mapped[str] = mapped_column(String(10), default="te-IN", nullable=False)
    service: Mapped[ServiceType | None] = mapped_column(
        Enum(ServiceType, name="service_type"), nullable=True
    )

    #: Qualification fields gathered so far, merged across turns.
    collected: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    turn_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_intent: Mapped[str | None] = mapped_column(String(40), nullable=True)

    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    turns = relationship(
        "ConversationTurn", back_populates="conversation", order_by="ConversationTurn.index"
    )


class ConversationTurn(Base):
    """A single utterance, kept for transcript review (MVP section 29)."""

    __tablename__ = "conversation_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), index=True, nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[Speaker] = mapped_column(Enum(Speaker, name="turn_speaker"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    conversation = relationship("Conversation", back_populates="turns")
