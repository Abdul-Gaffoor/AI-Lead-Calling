import datetime as dt

from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class SuppressionEntry(Base):
    """Do-not-call suppression list (MVP section 30).

    Every upload is checked against this table; a match blocks the lead with
    no manual dependency.
    """

    __tablename__ = "suppression_list"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(50), default="OPT_OUT", nullable=False)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    """Immutable record of user/system actions (MVP section 31)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
