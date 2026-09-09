import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, require_roles
from backend.auth.models import Role, User
from backend.auth.schemas import Token, UserCreate, UserOut
from backend.compliance.service import log_action
from backend.core.config import settings
from backend.core.database import get_db
from backend.core.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
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

    user.failed_login_attempts = 0
    user.locked_until = None
    db.commit()
    return Token(access_token=create_access_token(str(user.id), user.role.value))


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
