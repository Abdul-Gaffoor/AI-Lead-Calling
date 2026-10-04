"""Security hardening (MVP §32).

The four items §32 lists that the platform did not have: MFA for
administrators, API rate limiting, PII masking in logs, and database
backups.
"""

import base64
import datetime as dt
import logging

import pytest

from backend.auth import mfa as totp
from backend.auth.models import Role, User
from backend.core import backup, ratelimit
from backend.core.logging_filters import PIIFilter, mask
from backend.core.database import SessionLocal
from tests.conftest import _create_user, _token


@pytest.fixture(autouse=True)
def clear_rate_limits():
    ratelimit.reset()
    yield
    ratelimit.reset()


# --- TOTP ----------------------------------------------------------------


@pytest.mark.parametrize(
    "when,expected",
    [
        (59, "287082"),
        (1111111109, "081804"),
        (1234567890, "005924"),
        (2000000000, "279037"),
    ],
)
def test_totp_matches_the_rfc_6238_test_vectors(when, expected):
    """
    Published vectors from RFC 6238 appendix B, SHA-1. If this fails the
    implementation is wrong, however plausible its output looks — a TOTP
    that is self-consistent but non-standard works with no real
    authenticator app.
    """
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp.code_at(secret, when) == expected


def test_a_code_from_the_neighbouring_step_is_accepted():
    """Phone clocks drift; rejecting on 20 seconds teaches people to turn MFA off."""
    secret = totp.generate_secret()
    now = 1_700_000_000
    assert totp.verify(secret, totp.code_at(secret, now - 30), when=now)
    assert totp.verify(secret, totp.code_at(secret, now + 30), when=now)


def test_a_code_from_two_steps_away_is_rejected():
    secret = totp.generate_secret()
    now = 1_700_000_000
    assert not totp.verify(secret, totp.code_at(secret, now - 90), when=now)


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "  ", None])
def test_malformed_codes_are_rejected(bad):
    assert not totp.verify(totp.generate_secret(), bad)


def test_a_recovery_code_works_once():
    codes = totp.generate_recovery_codes()
    hashed = [totp.hash_recovery_code(c) for c in codes]

    remaining = totp.consume_recovery_code(codes[2], hashed)
    assert remaining is not None and len(remaining) == len(hashed) - 1
    assert totp.consume_recovery_code(codes[2], remaining) is None


def test_recovery_codes_are_not_stored_in_the_clear():
    codes = totp.generate_recovery_codes()
    hashed = [totp.hash_recovery_code(c) for c in codes]
    assert not set(codes) & set(hashed)


# --- MFA over the API -----------------------------------------------------


def test_an_administrator_cannot_log_in_with_a_password_alone(client, admin_user):
    response = client.post(
        "/auth/login", data={"username": admin_user.email, "password": "password123"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "mfa_required"


def test_a_wrong_code_counts_towards_the_lockout(client, admin_user):
    """
    Otherwise the one-time code is the single credential on the account
    that can be guessed without limit.
    """
    from backend.core.config import settings

    for _ in range(settings.max_failed_logins):
        client.post(
            "/auth/login",
            data={
                "username": admin_user.email,
                "password": "password123",
                "mfa_code": "000000",
            },
        )

    response = client.post(
        "/auth/login",
        data={
            "username": admin_user.email,
            "password": "password123",
            "mfa_code": totp.code_at(admin_user.mfa_secret),
        },
    )
    assert response.status_code == 423, "a locked account should stay locked"


def test_a_recovery_code_logs_an_administrator_in(client, admin_user):
    codes = totp.generate_recovery_codes(3)
    with SessionLocal() as db:
        user = db.get(User, admin_user.id)
        user.mfa_recovery_codes = [totp.hash_recovery_code(c) for c in codes]
        db.commit()

    response = client.post(
        "/auth/login",
        data={
            "username": admin_user.email,
            "password": "password123",
            "mfa_code": codes[0],
        },
    )
    assert response.status_code == 200, response.text

    # ...and only once.
    again = client.post(
        "/auth/login",
        data={
            "username": admin_user.email,
            "password": "password123",
            "mfa_code": codes[0],
        },
    )
    assert again.status_code == 401


def test_an_unenrolled_administrator_gets_an_enrolment_token_not_a_lockout(client):
    """
    Turning §32 on for a live deployment must not lock out the very
    accounts that would have to fix it. The password still has to be
    right; what comes back opens enrolment and nothing else.
    """
    user = _create_user("fresh-admin@swarajsolar.com", Role.SUPER_ADMIN)
    with SessionLocal() as db:
        stored = db.get(User, user.id)
        stored.mfa_enabled = False
        stored.mfa_secret = None
        db.commit()

    response = client.post(
        "/auth/login",
        data={"username": user.email, "password": "password123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "mfa_enrolment"

    headers = {"Authorization": f"Bearer {body['access_token']}"}

    # It opens enrolment...
    setup = client.post("/auth/mfa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    assert setup.json()["secret"]
    assert len(setup.json()["recovery_codes"]) == 10

    # ...and nothing else.
    assert client.get("/leads", headers=headers).status_code == 403
    assert client.get("/auth/me", headers=headers).status_code == 403


def test_enrolment_completes_and_then_the_session_is_full(client):
    user = _create_user("enrolling@swarajsolar.com", Role.SUPER_ADMIN)
    with SessionLocal() as db:
        stored = db.get(User, user.id)
        stored.mfa_enabled = False
        stored.mfa_secret = None
        db.commit()

    login = client.post(
        "/auth/login", data={"username": user.email, "password": "password123"}
    ).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    secret = client.post("/auth/mfa/setup", headers=headers).json()["secret"]
    confirm = client.post(
        "/auth/mfa/confirm", headers=headers, json={"code": totp.code_at(secret)}
    )
    assert confirm.status_code == 200, confirm.text

    full = client.post(
        "/auth/login",
        data={
            "username": user.email,
            "password": "password123",
            "mfa_code": totp.code_at(secret),
        },
    )
    assert full.status_code == 200
    assert full.json()["scope"] == "full"


def test_a_wrong_code_does_not_complete_enrolment(client):
    user = _create_user("sloppy@swarajsolar.com", Role.SUPER_ADMIN)
    with SessionLocal() as db:
        stored = db.get(User, user.id)
        stored.mfa_enabled = False
        stored.mfa_secret = None
        db.commit()

    login = client.post(
        "/auth/login", data={"username": user.email, "password": "password123"}
    ).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    client.post("/auth/mfa/setup", headers=headers)

    assert (
        client.post(
            "/auth/mfa/confirm", headers=headers, json={"code": "000000"}
        ).status_code
        == 400
    )


def test_an_administrator_cannot_switch_their_own_mfa_off(client, admin_headers, admin_user):
    """A control the controlled party can disable is not a control."""
    response = client.post(
        "/auth/mfa/disable",
        headers=admin_headers,
        json={"code": totp.code_at(admin_user.mfa_secret)},
    )
    assert response.status_code == 403


def test_a_non_administrator_is_not_forced_into_mfa(client):
    """§32 asks for MFA on administrators, not on every sales executive."""
    user = _create_user("exec@swarajsolar.com", Role.SALES_EXECUTIVE)
    response = client.post(
        "/auth/login", data={"username": user.email, "password": "password123"}
    )
    assert response.status_code == 200
    assert response.json()["scope"] == "full"


# --- rate limiting --------------------------------------------------------


def test_login_is_rate_limited(client, admin_user, monkeypatch):
    from backend.core import config

    monkeypatch.setattr(config.settings, "login_rate_limit_per_minute", 5)

    codes = [
        client.post(
            "/auth/login",
            data={"username": admin_user.email, "password": "wrong"},
        ).status_code
        for _ in range(8)
    ]
    assert 429 in codes, f"login was never throttled: {codes}"


def test_the_health_check_is_never_rate_limited(client, monkeypatch):
    """
    A throttled API must still be able to tell the deploy and Docker that
    it is alive, or rate limiting takes the service down by itself.
    """
    from backend.core import config

    monkeypatch.setattr(config.settings, "rate_limit_per_minute", 2)
    codes = [client.get("/health").status_code for _ in range(10)]
    assert set(codes) == {200}


def test_the_general_limit_applies_to_the_api(client, admin_headers, monkeypatch):
    from backend.core import config

    monkeypatch.setattr(config.settings, "rate_limit_per_minute", 3)
    codes = [client.get("/leads", headers=admin_headers).status_code for _ in range(8)]
    assert 429 in codes


def test_a_limit_of_zero_disables_the_check(client, monkeypatch):
    from backend.core import config

    monkeypatch.setattr(config.settings, "rate_limit_per_minute", 0)
    codes = [client.get("/solar/assumptions").status_code for _ in range(20)]
    assert 429 not in codes


# --- PII masking ----------------------------------------------------------


@pytest.mark.parametrize(
    "line,must_not_contain",
    [
        ("Dialling +919876543210 now", "9876543210"),
        ("lead phone 9876543210 failed", "9876543210"),
        ("to_number=09876543210", "9876543210"),
        ("+91 98765 43210 did not answer", "98765 43210"),
        ("user abdul.cloud1@gmail.com signed in", "abdul.cloud1"),
        ("aadhaar 1234 5678 9012 supplied", "1234 5678 9012"),
    ],
)
def test_identifiers_do_not_survive_masking(line, must_not_contain):
    assert must_not_contain not in mask(line)


@pytest.mark.parametrize(
    "line",
    [
        "system size 5 kW, bill 7500 per month",
        "campaign 42 dispatched 1886 leads",
        "score 92 classification HOT",
    ],
)
def test_masking_leaves_operational_numbers_alone(line):
    """A masker that eats every number makes the logs useless."""
    assert mask(line) == line


def test_the_filter_masks_both_the_message_and_its_arguments(caplog):
    logger = logging.getLogger("test.pii")
    handler = logging.StreamHandler()
    handler.addFilter(PIIFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    record = logging.LogRecord(
        name="test.pii",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="calling %s for %s",
        args=("+919876543210", "ramesh@example.com"),
        exc_info=None,
    )
    PIIFilter().filter(record)
    rendered = record.getMessage()
    assert "9876543210" not in rendered
    assert "ramesh@example.com" not in rendered


def test_the_filter_never_loses_a_log_line():
    """A filter that raises destroys the record it was protecting."""
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg=object(), args=None, exc_info=None,  # not a string
    )
    assert PIIFilter().filter(record) is True


# --- database backups -----------------------------------------------------


def test_a_backup_key_round_trips_through_its_timestamp():
    now = dt.datetime(2026, 10, 4, 2, 30, tzinfo=dt.timezone.utc)
    key = f"backups/swaraj_solar-{now.strftime('%Y%m%dT%H%M%SZ')}.dump"
    assert backup._stamp_of(key) == now


@pytest.mark.parametrize(
    "key", ["backups/notes.txt", "backups/", "backups/no-stamp.dump", "other/x.dump"]
)
def test_unrecognised_keys_are_left_alone_by_the_pruner(key):
    """Deleting something unexpected is the worse mistake."""
    assert backup._stamp_of(key) is None


def test_pruning_removes_only_what_is_past_retention(monkeypatch):
    from backend.core import config

    monkeypatch.setattr(config.settings, "backup_retention_days", 7)
    now = dt.datetime(2026, 10, 4, tzinfo=dt.timezone.utc)

    class FakeStorage:
        def __init__(self):
            self.keys = [
                "backups/db-20260925T020000Z.dump",  # 9 days — goes
                "backups/db-20261001T020000Z.dump",  # 3 days — stays
                "backups/db-20261003T020000Z.dump",  # 1 day  — stays
                "backups/readme.txt",                # unknown — stays
            ]
            self.deleted = []

        def list(self, prefix):
            return [k for k in self.keys if k.startswith(prefix)]

        def delete(self, key):
            self.deleted.append(key)

    fake = FakeStorage()
    monkeypatch.setattr(backup, "get_storage_provider", lambda: fake)

    assert backup.prune(now=now) == 1
    assert fake.deleted == ["backups/db-20260925T020000Z.dump"]


def test_retention_of_zero_keeps_everything(monkeypatch):
    from backend.core import config

    monkeypatch.setattr(config.settings, "backup_retention_days", 0)
    assert backup.prune() == 0


def test_a_sqlite_deployment_is_told_backups_need_postgres(monkeypatch):
    from backend.core import config

    monkeypatch.setattr(config.settings, "database_url", "sqlite:///./test.db")
    with pytest.raises(backup.BackupError, match="PostgreSQL"):
        backup.run_backup()


def test_the_backup_password_never_reaches_the_command_line(monkeypatch):
    """`ps` shows a command line to every process on the host."""
    from backend.core import config

    monkeypatch.setattr(
        config.settings,
        "database_url",
        "postgresql+psycopg2://swaraj:s3cr3t-p%40ss@db:5432/swaraj_solar",
    )
    captured = {}

    def fake_run(command, env=None, **kwargs):
        captured["command"] = command
        captured["env"] = env
        raise FileNotFoundError("pg_dump")

    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    with pytest.raises(backup.BackupError):
        backup.run_backup()

    assert not any("s3cr3t" in part for part in captured["command"])
    assert captured["env"]["PGPASSWORD"]
