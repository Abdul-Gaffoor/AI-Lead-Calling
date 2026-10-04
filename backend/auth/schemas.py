import datetime as dt

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from backend.auth.models import Role


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    #: "full" for a normal session. "mfa_enrolment" means the password was
    #: right but the account must finish setting up MFA before the token
    #: opens anything else (MVP §32).
    scope: str = "full"
    detail: str | None = None


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    role: Role


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    role: Role
    is_active: bool
    created_at: dt.datetime


class MfaSetup(BaseModel):
    """Everything the user needs to enrol, shown exactly once."""

    secret: str
    otpauth_uri: str
    recovery_codes: list[str]


class MfaCode(BaseModel):
    code: str
