import datetime as dt
import enum

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base
from backend.leads.models import ServiceType


class SurveyStatus(str, enum.Enum):
    """Site survey workflow (MVP section 25)."""

    REQUESTED = "REQUESTED"
    SCHEDULED = "SCHEDULED"
    ASSIGNED = "ASSIGNED"
    VISITED = "VISITED"
    COMPLETED = "COMPLETED"
    QUOTATION_REQUIRED = "QUOTATION_REQUIRED"
    CANCELLED = "CANCELLED"


class SiteSurvey(Base):
    __tablename__ = "site_surveys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"), index=True, nullable=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"), index=True, nullable=False
    )
    service: Mapped[ServiceType | None] = mapped_column(
        Enum(ServiceType, name="service_type"), nullable=True
    )
    status: Mapped[SurveyStatus] = mapped_column(
        Enum(SurveyStatus, name="survey_status"),
        default=SurveyStatus.REQUESTED,
        index=True,
        nullable=False,
    )

    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Only stored when the customer gave permission (MVP section 25).
    gps_latitude: Mapped[float | None] = mapped_column(nullable=True)
    gps_longitude: Mapped[float | None] = mapped_column(nullable=True)

    preferred_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    preferred_time: Mapped[str | None] = mapped_column(String(30), nullable=True)
    scheduled_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assigned_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=True
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
