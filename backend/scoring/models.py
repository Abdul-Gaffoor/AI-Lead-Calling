import datetime as dt

from sqlalchemy import JSON, DateTime, Enum, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base
from backend.leads.models import ServiceType


class ScoringConfig(Base):
    """Admin-configurable scoring rules for one service (MVP section 20)."""

    __tablename__ = "scoring_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[ServiceType] = mapped_column(
        Enum(ServiceType, name="service_type"), unique=True, index=True, nullable=False
    )
    rules: Mapped[list] = mapped_column(JSON, nullable=False)
    #: [[min_score, label], ...] highest first.
    bands: Mapped[list] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
