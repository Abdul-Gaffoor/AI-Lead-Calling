import datetime as dt
import enum

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class Role(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    SALES_MANAGER = "SALES_MANAGER"
    LEAD_OPERATOR = "LEAD_OPERATOR"
    SALES_EXECUTIVE = "SALES_EXECUTIVE"
    SERVICE_EXECUTIVE = "SERVICE_EXECUTIVE"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role, name="user_role"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Account lockout (security requirement, MVP section 32)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
