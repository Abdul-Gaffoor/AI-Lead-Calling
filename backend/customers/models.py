import datetime as dt

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class Customer(Base):
    """One record per real person/phone number.

    Daily lead uploads attach to an existing customer instead of creating a
    new identity each time (MVP section 4 — customer history).
    """

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    opted_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    opted_out_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Existing Swaraj installation — service/maintenance flows treat these differently.
    is_existing_customer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    leads = relationship("Lead", back_populates="customer", order_by="Lead.id")
