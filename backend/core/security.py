import datetime as dt

import jwt
from passlib.context import CryptContext

from backend.core.config import settings

ALGORITHM = "HS256"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


#: A normal session.
SCOPE_FULL = "full"
#: Issued to an administrator whose role requires MFA but who has not
#: enrolled yet. It opens the enrolment endpoints and nothing else.
#: Without it, turning MFA on for a role would lock out every account in
#: that role, because the setup endpoint itself needs a token.
SCOPE_MFA_ENROLMENT = "mfa_enrolment"


def create_access_token(subject: str, role: str, scope: str = SCOPE_FULL) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "scope": scope,
        "iat": now,
        "exp": now + dt.timedelta(minutes=settings.jwt_expires_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and verify a JWT. Raises jwt.PyJWTError on any failure."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
