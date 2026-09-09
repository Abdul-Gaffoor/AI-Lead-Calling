import os

# Configure the app for testing BEFORE any backend import.
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET", "test-secret-0123456789-abcdefghijklmnop")

import pytest
from fastapi.testclient import TestClient

from backend.core.database import Base, SessionLocal, engine
from backend.core.security import hash_password
from backend.main import app
from backend.auth.models import Role, User


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
    with SessionLocal() as session:
        user = User(
            email=email,
            full_name=email.split("@")[0].title(),
            hashed_password=hash_password(password),
            role=role,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def _token(client: TestClient, email: str, password: str = "password123") -> str:
    response = client.post("/auth/login", data={"username": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


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
