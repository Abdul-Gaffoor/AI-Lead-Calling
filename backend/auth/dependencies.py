import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from backend.auth.models import Role, User
from backend.core.database import get_db
from backend.core.security import SCOPE_FULL, SCOPE_MFA_ENROLMENT, decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

_credentials_error = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


_enrolment_only_error = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Finish multi-factor enrolment before using the platform",
)


def _user_from(token: str, db: Session) -> tuple[User, str]:
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _credentials_error
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _credentials_error
    # Tokens issued before scopes existed are full sessions.
    return user, payload.get("scope", SCOPE_FULL)


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    user, scope = _user_from(token, db)
    if scope != SCOPE_FULL:
        # An enrolment token must not reach a lead, a campaign or a
        # recording. It exists only to let somebody finish setting up MFA.
        raise _enrolment_only_error
    return user


def get_enrolling_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    """A user holding either a full session or an enrolment token.

    Only the MFA setup and confirm endpoints accept this.
    """
    user, _ = _user_from(token, db)
    return user


def require_roles(*roles: Role):
    def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions for this action",
            )
        return current_user

    return checker
