"""Live transfer to a sales executive (MVP §23).

The fallback matters more than the happy path. A customer who asked for a
person and got silence is worse off than one who was never called: they
asked, the system acknowledged, and nothing happened.
"""

import io

import pytest

from backend.auth.models import Role, User
from backend.calls.models import CallAttempt, Disposition
from backend.core.database import SessionLocal
from backend.leads.models import ServiceType
from backend.sales.transfer import (
    Outcome,
    available_executives,
    pick_executive,
    transfer_to_executive,
)
from backend.telephony.base import TelephonyError
from backend.telephony.factory import get_telephony_provider, reset_provider_cache
from tests.conftest import _create_user

HEADERS = "Customer_Name,Mobile_Number,Lead_Source,Consent_Status,City,Monthly_Bill\n"
ALL_DAY = {"window_start": "00:00:00", "window_end": "23:59:59"}


@pytest.fixture(autouse=True)
def fresh_provider():
    reset_provider_cache()
    yield
    reset_provider_cache()


@pytest.fixture
def answered_call(client, admin_headers):
    csv = HEADERS + "Ramesh,9876543210,WEBSITE,YES,Miyapur,7500\n"
    upload = client.post(
        "/leads/uploads", headers=admin_headers,
        files={"file": ("leads.csv", io.BytesIO(csv.encode()), "text/csv")},
    ).json()
    campaign = client.post(
        "/campaigns", headers=admin_headers,
        json={"name": "Transfer test", "concurrency": 2, "max_attempts": 3, **ALL_DAY},
    ).json()
    client.post(
        f"/campaigns/{campaign['id']}/leads", headers=admin_headers,
        json={"upload_id": upload["id"]},
    )
    client.post(f"/campaigns/{campaign['id']}/start", headers=admin_headers)
    client.post(f"/campaigns/{campaign['id']}/dispatch", headers=admin_headers)
    return client.get("/calls", headers=admin_headers).json()[0]


def _make_executive(email: str, *, phone="+919000000001", available=True, role=Role.SALES_EXECUTIVE):
    user = _create_user(email, role)
    with SessionLocal() as db:
        stored = db.get(User, user.id)
        stored.phone = phone
        stored.available_for_transfer = available
        db.commit()
    return user


# --- who is available -----------------------------------------------------


def test_an_executive_without_a_number_is_not_available():
    """Marked available with no number is a transfer that fails at the provider."""
    _make_executive("nophone@swarajsolar.com", phone=None)
    with SessionLocal() as db:
        assert available_executives(db) == []


def test_an_executive_who_is_not_at_their_desk_is_not_available():
    """`is_active` means "may sign in", which is a different question."""
    _make_executive("away@swarajsolar.com", available=False)
    with SessionLocal() as db:
        assert available_executives(db) == []


def test_an_inactive_account_is_not_available():
    user = _make_executive("gone@swarajsolar.com")
    with SessionLocal() as db:
        db.get(User, user.id).is_active = False
        db.commit()
        assert available_executives(db) == []


def test_the_least_loaded_executive_is_picked():
    first = _make_executive("first@swarajsolar.com", phone="+919000000001")
    second = _make_executive("second@swarajsolar.com", phone="+919000000002")

    from backend.customers.models import Customer
    from backend.leads.models import Lead, LeadStatus
    from backend.sales.models import Opportunity, OpportunityStage

    with SessionLocal() as db:
        customer = Customer(phone="+919812345678", name="Load")
        db.add(customer); db.flush()
        lead = Lead(
            customer_id=customer.id, source="TEST",
            consent_status="YES", status=LeadStatus.READY,
        )
        db.add(lead); db.flush()
        db.add(
            Opportunity(
                lead_id=lead.id,
                customer_id=customer.id,
                assigned_to_id=first.id,
                stage=OpportunityStage.NEW,
                score=50,
                classification="WARM",
            )
        )
        db.commit()
        assert pick_executive(db).id == second.id


def test_a_senior_escalation_prefers_a_manager():
    _make_executive("exec@swarajsolar.com", phone="+919000000001")
    manager = _make_executive(
        "mgr@swarajsolar.com", phone="+919000000009", role=Role.SALES_MANAGER
    )
    with SessionLocal() as db:
        assert pick_executive(db, senior=True).id == manager.id


def test_a_senior_escalation_falls_back_to_the_normal_queue():
    """Reaching somebody beats reaching nobody because managers are busy."""
    executive = _make_executive("exec2@swarajsolar.com", phone="+919000000001")
    with SessionLocal() as db:
        assert pick_executive(db, senior=True).id == executive.id


# --- transferring ---------------------------------------------------------


def test_a_call_is_handed_to_an_available_executive(answered_call):
    executive = _make_executive("taker@swarajsolar.com", phone="+919000000007")

    with SessionLocal() as db:
        attempt = db.get(CallAttempt, answered_call["id"])
        result = transfer_to_executive(db, attempt)
        connected_id = result.executive.id if result.executive else None
        db.commit()

    assert result.outcome is Outcome.CONNECTED
    assert connected_id == executive.id

    provider = get_telephony_provider()
    assert provider.transfers, "the provider was never asked to transfer"
    assert provider.transfers[-1][1] == "+919000000007"


def test_with_nobody_available_a_priority_callback_is_booked(answered_call):
    """
    MVP §23: "If nobody is available: create PRIORITY CALLBACK". Silence is
    not an option — the customer asked and the system acknowledged.
    """
    with SessionLocal() as db:
        attempt = db.get(CallAttempt, answered_call["id"])
        result = transfer_to_executive(db, attempt)
        db.commit()

    assert result.outcome is Outcome.CALLBACK
    assert result.callback_at is not None

    with SessionLocal() as db:
        attempt = db.get(CallAttempt, answered_call["id"])
        assert attempt.disposition is Disposition.CALLBACK_REQUESTED


def test_a_provider_failure_becomes_a_callback_not_an_error(answered_call, monkeypatch):
    """The customer is still on the line; an exception helps nobody."""
    _make_executive("unreachable@swarajsolar.com", phone="+919000000008")

    provider = get_telephony_provider()
    monkeypatch.setattr(
        provider,
        "transfer",
        lambda *a, **k: (_ for _ in ()).throw(TelephonyError("channel busy")),
    )

    with SessionLocal() as db:
        attempt = db.get(CallAttempt, answered_call["id"])
        result = transfer_to_executive(db, attempt)
        db.commit()

    assert result.outcome is Outcome.CALLBACK
    assert "channel busy" in result.reason

    with SessionLocal() as db:
        assert (
            db.get(CallAttempt, answered_call["id"]).disposition
            is Disposition.CALLBACK_REQUESTED
        )


def test_asking_for_a_person_on_a_call_triggers_the_transfer(
    client, admin_headers, answered_call
):
    """End to end through the orchestrator, not the service in isolation."""
    executive = _make_executive("live@swarajsolar.com", phone="+919000000011")

    started = client.post(
        "/ai/conversations", headers=admin_headers, json={"call_id": answered_call["id"]}
    )
    assert started.status_code == 201, started.text
    conversation = started.json()
    reply = client.post(
        f"/ai/conversations/{conversation['conversation_id']}/turn",
        headers=admin_headers,
        json={"text": "sales person tho maatladali"},
    )
    assert reply.status_code == 200, reply.text

    provider = get_telephony_provider()
    assert provider.transfers, "HUMAN_REQUEST did not reach the transfer service"
    assert provider.transfers[-1][1] == "+919000000011"


def test_the_summary_says_what_happened_to_the_customer(
    client, admin_headers, answered_call
):
    """
    The executive reading the summary needs to know whether the customer
    was connected or is waiting for a callback.
    """
    started = client.post(
        "/ai/conversations", headers=admin_headers, json={"call_id": answered_call["id"]}
    )
    assert started.status_code == 201, started.text
    conversation = started.json()
    client.post(
        f"/ai/conversations/{conversation['conversation_id']}/turn",
        headers=admin_headers,
        json={"text": "sales person tho maatladali"},
    )

    detail = client.get(
        f"/ai/conversations/{conversation['conversation_id']}", headers=admin_headers
    ).json()
    assert "callback" in detail["summary"].lower()


# --- the Exotel implementation -------------------------------------------


def test_exotel_transfer_refuses_without_a_call_id(monkeypatch):
    from backend.telephony.exotel import ExotelProvider

    monkeypatch.setattr("backend.core.config.settings.exotel_sid", "sid")
    monkeypatch.setattr("backend.core.config.settings.exotel_api_key", "key")
    monkeypatch.setattr("backend.core.config.settings.exotel_api_token", "token")
    monkeypatch.setattr("backend.core.config.settings.exotel_caller_id", "+911234567890")
    monkeypatch.setattr("backend.core.config.settings.exotel_flow_app_id", "123")

    provider = ExotelProvider()
    with pytest.raises(TelephonyError, match="provider call id"):
        provider.transfer("", "+919000000001")
    with pytest.raises(TelephonyError, match="destination number"):
        provider.transfer("call-sid", "")


def test_exotel_connect_mode_dials_the_executive(monkeypatch):
    from backend.telephony.exotel import ExotelProvider

    for key, value in {
        "exotel_sid": "sid", "exotel_api_key": "key", "exotel_api_token": "token",
        "exotel_caller_id": "+911234567890", "exotel_flow_app_id": "123",
        "exotel_transfer_mode": "connect",
    }.items():
        monkeypatch.setattr(f"backend.core.config.settings.{key}", value)

    sent = {}
    provider = ExotelProvider()
    monkeypatch.setattr(
        provider, "_post", lambda path, data: sent.update(path=path, data=data) or {}
    )

    provider.transfer("call-sid", "+919000000001")
    assert sent["data"]["CallSid"] == "call-sid"
    assert sent["data"]["To"] == "+919000000001"


def test_exotel_flow_mode_needs_a_flow_app_id(monkeypatch):
    from backend.telephony.exotel import ExotelProvider

    for key, value in {
        "exotel_sid": "sid", "exotel_api_key": "key", "exotel_api_token": "token",
        "exotel_caller_id": "+911234567890", "exotel_flow_app_id": "123",
        "exotel_transfer_mode": "flow", "exotel_transfer_flow_app_id": "",
    }.items():
        monkeypatch.setattr(f"backend.core.config.settings.{key}", value)

    with pytest.raises(TelephonyError, match="EXOTEL_TRANSFER_FLOW_APP_ID"):
        ExotelProvider().transfer("call-sid", "+919000000001")


def test_an_unknown_transfer_mode_is_refused(monkeypatch):
    from backend.telephony.exotel import ExotelProvider

    for key, value in {
        "exotel_sid": "sid", "exotel_api_key": "key", "exotel_api_token": "token",
        "exotel_caller_id": "+911234567890", "exotel_flow_app_id": "123",
        "exotel_transfer_mode": "telepathy",
    }.items():
        monkeypatch.setattr(f"backend.core.config.settings.{key}", value)

    with pytest.raises(TelephonyError, match="Unknown EXOTEL_TRANSFER_MODE"):
        ExotelProvider().transfer("call-sid", "+919000000001")


def test_the_disposition_tells_the_truth_about_what_happened(
    client, admin_headers, answered_call
):
    """
    MVP §27 puts "Human Transfers" on the manager's dashboard. A transfer
    that never happened must not be counted as one, or the number tells a
    manager customers reached a person when they did not.
    """
    _make_executive("counted@swarajsolar.com", phone="+919000000012")

    started = client.post(
        "/ai/conversations", headers=admin_headers, json={"call_id": answered_call["id"]}
    )
    assert started.status_code == 201, started.text
    client.post(
        f"/ai/conversations/{started.json()['conversation_id']}/turn",
        headers=admin_headers,
        json={"text": "sales person tho maatladali"},
    )

    with SessionLocal() as db:
        attempt = db.get(CallAttempt, answered_call["id"])
        assert attempt.disposition is Disposition.HUMAN_TRANSFER, (
            "a completed transfer should be recorded as one"
        )
