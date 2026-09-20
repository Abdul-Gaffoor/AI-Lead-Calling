"""Human review of AI calls (MVP section 29).

Managers listen back, read the transcript and mark what the AI got wrong. The
point is not record-keeping: these flags are the measurement behind the MVP's
success criteria — Telugu understanding accuracy, field-capture accuracy,
lead-classification accuracy — so they are stored as fixed codes that can be
counted, not as free text.
"""

import datetime as dt
import enum

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class ReviewVerdict(str, enum.Enum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"


class ReviewFlag(str, enum.Enum):
    """The specific faults from MVP section 29."""

    WRONG_TRANSCRIPTION = "WRONG_TRANSCRIPTION"
    WRONG_QUALIFICATION = "WRONG_QUALIFICATION"
    WRONG_LANGUAGE = "WRONG_LANGUAGE"
    WRONG_LEAD_SCORE = "WRONG_LEAD_SCORE"
    BAD_AI_RESPONSE = "BAD_AI_RESPONSE"


class CallReview(Base):
    """One reviewer's verdict on one call."""

    __tablename__ = "call_reviews"
    __table_args__ = (
        # One verdict per person per call — a reviewer changing their mind
        # updates their review rather than adding a second one.
        UniqueConstraint("call_attempt_id", "reviewer_id", name="uq_review_per_reviewer"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_attempt_id: Mapped[int] = mapped_column(
        ForeignKey("call_attempts.id"), index=True, nullable=False
    )
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)

    verdict: Mapped[ReviewVerdict] = mapped_column(
        Enum(ReviewVerdict, name="review_verdict"), nullable=False
    )
    #: ReviewFlag values. A list rather than one column per fault, so adding a
    #: fault type later is not a migration on every review row.
    flags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    call_attempt = relationship("CallAttempt")
