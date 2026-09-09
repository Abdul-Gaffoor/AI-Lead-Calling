import datetime as dt
import enum

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class LeadStatus(str, enum.Enum):
    READY = "READY"  # validated, eligible for a campaign
    IN_CAMPAIGN = "IN_CAMPAIGN"  # picked up by a campaign (Sprint 2)
    COMPLETED = "COMPLETED"  # calling finished / disposition recorded
    CLOSED = "CLOSED"  # manually closed


class ServiceType(str, enum.Enum):
    RESIDENTIAL_SOLAR = "RESIDENTIAL_SOLAR"
    PM_SURYA_GHAR = "PM_SURYA_GHAR"
    COMMERCIAL_SOLAR = "COMMERCIAL_SOLAR"
    INDUSTRIAL_SOLAR = "INDUSTRIAL_SOLAR"
    AGRICULTURE_SOLAR = "AGRICULTURE_SOLAR"
    GROUND_MOUNTED_SOLAR = "GROUND_MOUNTED_SOLAR"
    EXISTING_SOLAR_UPGRADE = "EXISTING_SOLAR_UPGRADE"
    SOLAR_MAINTENANCE = "SOLAR_MAINTENANCE"
    PANEL_CLEANING = "PANEL_CLEANING"
    GENERAL_ENQUIRY = "GENERAL_ENQUIRY"


class RejectionReason(str, enum.Enum):
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_PHONE = "INVALID_PHONE"
    DUPLICATE_IN_FILE = "DUPLICATE_IN_FILE"
    DUPLICATE_EXISTING = "DUPLICATE_EXISTING"
    CONSENT_MISSING = "CONSENT_MISSING"
    OPTED_OUT = "OPTED_OUT"


class UploadStatus(str, enum.Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class LeadUpload(Base):
    """One daily Excel/CSV import batch with its validation summary."""

    __tablename__ = "lead_uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[UploadStatus] = mapped_column(
        Enum(UploadStatus, name="upload_status"), default=UploadStatus.COMPLETED, nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    total_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invalid_phone_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    opted_out_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consent_issue_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    missing_field_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    leads = relationship("Lead", back_populates="upload")
    rejections = relationship("LeadRejection", back_populates="upload", order_by="LeadRejection.row_number")


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_ref: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)

    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True, nullable=False)
    upload_id: Mapped[int | None] = mapped_column(ForeignKey("lead_uploads.id"), nullable=True)

    source: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[LeadStatus] = mapped_column(
        Enum(LeadStatus, name="lead_status"), default=LeadStatus.READY, index=True, nullable=False
    )

    # Consent (MVP section 31 — store status, source and timestamp)
    consent_status: Mapped[str] = mapped_column(String(50), nullable=False)
    consent_date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    consent_source: Mapped[str | None] = mapped_column(String(100), nullable=True)

    interested_service: Mapped[ServiceType | None] = mapped_column(
        Enum(ServiceType, name="service_type"), nullable=True
    )
    language: Mapped[str | None] = mapped_column(String(30), nullable=True)
    alternate_number: Mapped[str | None] = mapped_column(String(20), nullable=True)

    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    district: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mandal: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pincode: Mapped[str | None] = mapped_column(String(10), nullable=True)

    monthly_bill: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    monthly_units: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)

    campaign_hint: Mapped[str | None] = mapped_column(String(100), nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    customer = relationship("Customer", back_populates="leads")
    upload = relationship("LeadUpload", back_populates="leads")


class LeadRejection(Base):
    """A rejected upload row, kept so operators can download rejection reasons."""

    __tablename__ = "lead_rejections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("lead_uploads.id"), index=True, nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mobile_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reason: Mapped[RejectionReason] = mapped_column(
        Enum(RejectionReason, name="rejection_reason"), nullable=False
    )
    details: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    upload = relationship("LeadUpload", back_populates="rejections")
