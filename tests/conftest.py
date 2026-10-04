import os

# Configure the app for testing BEFORE any backend import.
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET", "test-secret-0123456789-abcdefghijklmnop")

import pytest
from fastapi.testclient import TestClient

from sqlalchemy import select

from backend.auth import mfa as totp
from backend.auth.router import mfa_required_for
from backend.core import ratelimit
from backend.core.database import Base, SessionLocal, engine
from backend.core.security import hash_password
from backend.main import app
from backend.auth.models import Role, User


@pytest.fixture(autouse=True)
def clean_rate_limits():
    """Every test starts with a fresh allowance.

    The limiter counts per process and per caller, and the whole suite is
    one process logging in from one address — so without this, tests
    throttle each other and the failure looks like whatever test happened
    to run four hundred requests in.
    """
    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    return TestClient(app)


def _create_user(email: str, role: Role, password: str = "password123") -> User:
    """A user, enrolled in MFA when their role requires it (MVP §32).

    Enrolling here rather than switching the requirement off means the
    whole suite logs in the way production does, second factor included,
    instead of exercising a configuration nobody runs.
    """
    with SessionLocal() as session:
        user = User(
            email=email,
            full_name=email.split("@")[0].title(),
            hashed_password=hash_password(password),
            role=role,
        )
        if mfa_required_for(role):
            user.mfa_secret = totp.generate_secret()
            user.mfa_enabled = True
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def _token(client: TestClient, email: str, password: str = "password123") -> str:
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.email == email))
        secret = user.mfa_secret if user and user.mfa_enabled else None

    data = {"username": email, "password": password}
    if secret:
        data["mfa_code"] = totp.code_at(secret)

    response = client.post("/auth/login", data=data)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("scope", "full") == "full", body
    return body["access_token"]


@pytest.fixture
def admin_user():
    return _create_user("admin@swarajsolar.com", Role.SUPER_ADMIN)


@pytest.fixture
def operator_user():
    return _create_user("operator@swarajsolar.com", Role.LEAD_OPERATOR)


@pytest.fixture
def executive_user():
    return _create_user("exec@swarajsolar.com", Role.SALES_EXECUTIVE)


@pytest.fixture
def admin_headers(client, admin_user):
    return {"Authorization": f"Bearer {_token(client, admin_user.email)}"}


@pytest.fixture
def operator_headers(client, operator_user):
    return {"Authorization": f"Bearer {_token(client, operator_user.email)}"}


@pytest.fixture
def executive_headers(client, executive_user):
    return {"Authorization": f"Bearer {_token(client, executive_user.email)}"}
