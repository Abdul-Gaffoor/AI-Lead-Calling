import datetime as dt

from fastapi import APIRouter, Depends, Form, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth import mfa as totp
from backend.auth.dependencies import get_current_user, get_enrolling_user, require_roles
from backend.auth.models import Role, User
from backend.auth.schemas import MfaCode, MfaSetup, Token, UserCreate, UserOut
from backend.compliance.service import log_action
from backend.core.config import settings
from backend.core.database import get_db
from backend.core.security import (
    SCOPE_MFA_ENROLMENT,
    create_access_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


#: Returned when the password was right but a second factor is still
#: needed, so the console can prompt for a code instead of telling the user
#: their password was wrong.
MFA_REQUIRED = "mfa_required"
MFA_ENROLMENT_REQUIRED = "mfa_enrolment_required"


def mfa_required_for(role: Role) -> bool:
    """Is MFA compulsory for this role? (MVP §32, administrators.)"""
    required = {
        name.strip().upper()
        for name in (settings.mfa_required_roles or "").split(",")
        if name.strip()
    }
    return role.value.upper() in required


@router.post("/login", response_model=Token)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    mfa_code: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.email == form.username.lower().strip()))
    now = dt.datetime.now(dt.timezone.utc)

    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")

    if user.locked_until is not None:
        locked_until = user.locked_until
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=dt.timezone.utc)
        if locked_until > now:
            raise HTTPException(
                status.HTTP_423_LOCKED,
                "Account temporarily locked due to failed login attempts",
            )

    if not verify_password(form.password, user.hashed_password):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.max_failed_logins:
            user.locked_until = now + dt.timedelta(minutes=settings.lockout_minutes)
            user.failed_login_attempts = 0
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")

    # Password is right. Everything below is the second factor, and a
    # failure here still counts against the lockout — otherwise the code
    # is the one credential on the account that can be brute-forced.
    if user.mfa_enabled:
        if not mfa_code:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, MFA_REQUIRED)

        remaining = totp.consume_recovery_code(mfa_code, user.mfa_recovery_codes or [])
        if remaining is not None:
            # A recovery code was spent. Record which, so an administrator
            # using their last ones is visible in the audit log.
            user.mfa_recovery_codes = remaining
            log_action(
                db,
                actor=user,
                action="auth.mfa.recovery_code_used",
                entity=f"user:{user.id}",
                details={"codes_remaining": len(remaining)},
            )
        elif not totp.verify(user.mfa_secret or "", mfa_code):
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings.max_failed_logins:
                user.locked_until = now + dt.timedelta(minutes=settings.lockout_minutes)
                user.failed_login_attempts = 0
            db.commit()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid authentication code")

    elif mfa_required_for(user.role):
        # §32 requires MFA on this role and this account has not enrolled.
        # Hand back a token scoped to enrolment and nothing else, so the
        # requirement can be switched on for a live deployment without
        # locking out the very accounts that would have to fix it.
        user.failed_login_attempts = 0
        user.locked_until = None
        db.commit()
        return Token(
            access_token=create_access_token(
                str(user.id), user.role.value, scope=SCOPE_MFA_ENROLMENT
            ),
            scope=SCOPE_MFA_ENROLMENT,
            detail=MFA_ENROLMENT_REQUIRED,
        )

    user.failed_login_attempts = 0
    user.locked_until = None
    db.commit()
    return Token(access_token=create_access_token(str(user.id), user.role.value))


# ---------------------------------------------------------------------------
# MFA enrolment (MVP §32)
# ---------------------------------------------------------------------------


@router.post("/mfa/setup", response_model=MfaSetup)
def mfa_setup(
    current_user: User = Depends(get_enrolling_user), db: Session = Depends(get_db)
):
    """Begin enrolment: issue a secret and the recovery codes.

    Nothing is enforced until `/mfa/confirm` proves the user can produce a
    code from this secret — issuing one and switching MFA on in the same
    step is how people lock themselves out of their own platform.
    """
    if current_user.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "MFA is already enabled")

    secret = totp.generate_secret()
    codes = totp.generate_recovery_codes()
    current_user.mfa_secret = secret
    current_user.mfa_recovery_codes = [totp.hash_recovery_code(c) for c in codes]
    db.commit()

    return MfaSetup(
        secret=secret,
        otpauth_uri=totp.provisioning_uri(
            secret, email=current_user.email, issuer=settings.mfa_issuer
        ),
        # Shown exactly once. They are hashed in the database, so nobody —
        # including an administrator with database access — can read them
        # back later.
        recovery_codes=codes,
    )


@router.post("/mfa/confirm", response_model=UserOut)
def mfa_confirm(
    body: MfaCode,
    current_user: User = Depends(get_enrolling_user),
    db: Session = Depends(get_db),
):
    """Finish enrolment by proving the authenticator app works."""
    if current_user.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "MFA is already enabled")
    if not current_user.mfa_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Start with /auth/mfa/setup")
    if not totp.verify(current_user.mfa_secret, body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid authentication code")

    current_user.mfa_enabled = True
    log_action(
        db, actor=current_user, action="auth.mfa.enabled",
        entity=f"user:{current_user.id}",
    )
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/mfa/disable", response_model=UserOut)
def mfa_disable(
    body: MfaCode,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Turn MFA off, which requires a current code.

    Roles where §32 makes MFA compulsory cannot turn it off at all: a
    setting an administrator can switch off for themselves is not a
    control.
    """
    if not current_user.mfa_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "MFA is not enabled")
    if mfa_required_for(current_user.role):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "MFA is mandatory for this role and cannot be disabled",
        )
    if not totp.verify(current_user.mfa_secret or "", body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid authentication code")

    current_user.mfa_enabled = False
    current_user.mfa_secret = None
    current_user.mfa_recovery_codes = None
    log_action(
        db, actor=current_user, action="auth.mfa.disabled",
        entity=f"user:{current_user.id}",
    )
    db.commit()
    db.refresh(current_user)
    return current_user


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post(
    "/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[],
)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(Role.SUPER_ADMIN)),
):
    email = payload.email.lower().strip()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
    user = User(
        email=email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.flush()
    log_action(db, actor=current_user, action="USER_CREATED", entity=f"user:{user.id}")
    db.commit()
    db.refresh(user)
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Role.SUPER_ADMIN, Role.SALES_MANAGER)),
):
    return db.scalars(select(User).order_by(User.id)).all()
