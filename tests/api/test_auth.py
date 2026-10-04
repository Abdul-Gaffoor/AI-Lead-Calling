from backend.core.config import settings


def test_login_success(client, admin_user):
    """An enrolled administrator logs in with password + code (MVP §32)."""
    from backend.auth import mfa as totp

    response = client.post(
        "/auth/login",
        data={
            "username": admin_user.email,
            "password": "password123",
            "mfa_code": totp.code_at(admin_user.mfa_secret),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["scope"] == "full"


def test_a_password_alone_does_not_get_an_administrator_in(client, admin_user):
    """The whole point of §32: the password is no longer sufficient."""
    response = client.post(
        "/auth/login", data={"username": admin_user.email, "password": "password123"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "mfa_required"


def test_login_wrong_password(client, admin_user):
    response = client.post(
        "/auth/login", data={"username": admin_user.email, "password": "wrong-password"}
    )
    assert response.status_code == 401


def test_login_unknown_user(client):
    response = client.post(
        "/auth/login", data={"username": "nobody@swarajsolar.com", "password": "whatever123"}
    )
    assert response.status_code == 401


def test_account_lockout_after_failed_attempts(client, admin_user):
    for _ in range(settings.max_failed_logins):
        response = client.post(
            "/auth/login", data={"username": admin_user.email, "password": "wrong-password"}
        )
        assert response.status_code == 401
    # Even the correct password is now rejected while locked
    response = client.post(
        "/auth/login", data={"username": admin_user.email, "password": "password123"}
    )
    assert response.status_code == 423


def test_me(client, admin_headers, admin_user):
    response = client.get("/auth/me", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == admin_user.email
    assert body["role"] == "SUPER_ADMIN"


def test_me_requires_token(client):
    assert client.get("/auth/me").status_code == 401


def test_me_rejects_bad_token(client):
    response = client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


def test_admin_can_create_user(client, admin_headers):
    response = client.post(
        "/auth/users",
        headers=admin_headers,
        json={
            "email": "manager@swarajsolar.com",
            "full_name": "Sales Manager",
            "password": "password123",
            "role": "SALES_MANAGER",
        },
    )
    assert response.status_code == 201
    assert response.json()["role"] == "SALES_MANAGER"

    # Duplicate email is rejected
    response = client.post(
        "/auth/users",
        headers=admin_headers,
        json={
            "email": "manager@swarajsolar.com",
            "full_name": "Duplicate",
            "password": "password123",
            "role": "SALES_MANAGER",
        },
    )
    assert response.status_code == 409


def test_operator_cannot_create_user(client, operator_headers):
    response = client.post(
        "/auth/users",
        headers=operator_headers,
        json={
            "email": "sneaky@swarajsolar.com",
            "full_name": "Sneaky",
            "password": "password123",
            "role": "SUPER_ADMIN",
        },
    )
    assert response.status_code == 403
