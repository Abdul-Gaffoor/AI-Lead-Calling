import datetime as dt
import io

import pytest

from backend.calls.models import CallAttempt, Disposition
from backend.campaigns.models import Campaign, CampaignLead, CampaignLeadStatus, CampaignStatus
from backend.campaigns.dispatcher import is_within_calling_window
from backend.core.database import SessionLocal
from backend.leads.models import Lead, LeadStatus
from backend.telephony.factory import get_telephony_provider, reset_provider_cache

HEADERS = "Customer_Name,Mobile_Number,Lead_Source,Consent_Status\n"
ALL_DAY = {"window_start": "00:00:00", "window_end": "23:59:59"}


@pytest.fixture(autouse=True)
def fresh_provider():
    """Each test gets a clean mock provider with no recorded calls."""
    reset_provider_cache()
    yield
    reset_provider_cache()


def upload_leads(client, headers, rows):
    csv_text = HEADERS + "".join(f"{name},{phone},EXCEL,YES\n" for name, phone in rows)
    response = client.post(
        "/leads/uploads",
        headers=headers,
        files={"file": ("leads.csv", io.BytesIO(csv_text.encode()), "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def make_campaign(client, headers, **overrides):
    payload = {"name": "September Residential", "concurrency": 2, "max_attempts": 3, **ALL_DAY}
    payload.update(overrides)
    response = client.post("/campaigns", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_campaign_lifecycle(client, admin_headers):
    campaign = make_campaign(client, admin_headers)
    campaign_id = campaign["id"]
    assert campaign["status"] == "DRAFT"

    # Cannot pause a campaign that never started
    assert client.post(f"/campaigns/{campaign_id}/pause", headers=admin_headers).status_code == 409

    for action, expected in [
        ("start", "RUNNING"),
        ("pause", "PAUSED"),
        ("resume", "RUNNING"),
        ("stop", "STOPPED"),
    ]:
        response = client.post(f"/campaigns/{campaign_id}/{action}", headers=admin_headers)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == expected

    # STOPPED is final
    assert client.post(f"/campaigns/{campaign_id}/start", headers=admin_headers).status_code == 409


def test_executive_cannot_manage_campaigns(client, executive_headers):
    response = client.post("/campaigns", headers=executive_headers, json={"name": "X"})
    assert response.status_code == 403


def test_add_leads_skips_duplicates_and_opt_outs(client, admin_headers, operator_headers):
    upload_id = upload_leads(
        client, operator_headers, [("Ramesh", "9876543210"), ("Krishna", "9444444444")]
    )
    client.post(
        "/compliance/opt-outs", headers=admin_headers, json={"phone": "9444444444"}
    )

    campaign_id = make_campaign(client, admin_headers)["id"]
    response = client.post(
        f"/campaigns/{campaign_id}/leads", headers=admin_headers, json={"upload_id": upload_id}
    )
    assert response.status_code == 200
    assert response.json() == {"added": 1, "skipped": 0, "suppressed": 1}

    # Adding the same upload again queues nothing new
    response = client.post(
        f"/campaigns/{campaign_id}/leads", headers=admin_headers, json={"upload_id": upload_id}
    )
    assert response.json()["added"] == 0

    # The queued lead moved out of READY so it cannot join another campaign
    with SessionLocal() as db:
        statuses = {lead.customer.phone: lead.status for lead in db.query(Lead).all()}
    assert statuses["+919876543210"] is LeadStatus.IN_CAMPAIGN


def test_add_leads_requires_exactly_one_source(client, admin_headers):
    campaign_id = make_campaign(client, admin_headers)["id"]
    response = client.post(f"/campaigns/{campaign_id}/leads", headers=admin_headers, json={})
    assert response.status_code == 422
    response = client.post(
        f"/campaigns/{campaign_id}/leads",
        headers=admin_headers,
        json={"upload_id": 1, "lead_ids": [1]},
    )
    assert response.status_code == 422


def test_dispatch_respects_concurrency_and_places_calls(
    client, admin_headers, operator_headers
):
    upload_id = upload_leads(
        client,
        operator_headers,
        [("A", "9876543210"), ("B", "9444444444"), ("C", "9555555555")],
    )
    campaign_id = make_campaign(client, admin_headers, concurrency=2)["id"]
    client.post(
        f"/campaigns/{campaign_id}/leads", headers=admin_headers, json={"upload_id": upload_id}
    )

    # A DRAFT campaign never dials
    response = client.post(f"/campaigns/{campaign_id}/dispatch", headers=admin_headers)
    assert response.json()["calls_placed"] == 0

    client.post(f"/campaigns/{campaign_id}/start", headers=admin_headers)
    response = client.post(f"/campaigns/{campaign_id}/dispatch", headers=admin_headers)
    assert response.json()["calls_placed"] == 2  # capped by concurrency

    # Slots are full, so a second tick places nothing
    response = client.post(f"/campaigns/{campaign_id}/dispatch", headers=admin_headers)
    assert response.json()["calls_placed"] == 0

    provider = get_telephony_provider()
    assert len(provider.placed_calls) == 2
    assert all(call.to_number.startswith("+91") for call in provider.placed_calls)

    calls = client.get(f"/calls?campaign_id={campaign_id}", headers=admin_headers).json()
    assert len(calls) == 2
    assert {c["state"] for c in calls} == {"QUEUED"}
    assert all(c["attempt_number"] == 1 for c in calls)


def test_no_answer_retries_then_exhausts(client, admin_headers, operator_headers):
    upload_id = upload_leads(client, operator_headers, [("A", "9876543210")])
    campaign_id = make_campaign(
        client, admin_headers, max_attempts=2, retry_rules={"NO_ANSWER": 0}
    )["id"]
    client.post(
        f"/campaigns/{campaign_id}/leads", headers=admin_headers, json={"upload_id": upload_id}
    )
    client.post(f"/campaigns/{campaign_id}/start", headers=admin_headers)

    for expected_attempt in (1, 2):
        assert (
            client.post(f"/campaigns/{campaign_id}/dispatch", headers=admin_headers).json()[
                "calls_placed"
            ]
            == 1
        )
        with SessionLocal() as db:
            attempt = db.query(CallAttempt).order_by(CallAttempt.id.desc()).first()
            assert attempt.attempt_number == expected_attempt
            call_id = attempt.provider_call_id

        response = client.post(
            f"/calls/webhooks/mock", json={"call_id": call_id, "status": "no-answer"}
        )
        assert response.status_code == 200

    # Two attempts used with no contact -> exhausted, no more dialling
    with SessionLocal() as db:
        entry = db.query(CampaignLead).one()
        assert entry.status is CampaignLeadStatus.EXHAUSTED
        assert entry.attempts == 2
        assert db.query(Lead).one().status is LeadStatus.COMPLETED

    assert (
        client.post(f"/campaigns/{campaign_id}/dispatch", headers=admin_headers).json()[
            "calls_placed"
        ]
        == 0
    )


def test_qualified_disposition_completes_lead(client, admin_headers, operator_headers):
    upload_id = upload_leads(client, operator_headers, [("Ramesh", "9876543210")])
    campaign_id = make_campaign(client, admin_headers)["id"]
    client.post(
        f"/campaigns/{campaign_id}/leads", headers=admin_headers, json={"upload_id": upload_id}
    )
    client.post(f"/campaigns/{campaign_id}/start", headers=admin_headers)
    client.post(f"/campaigns/{campaign_id}/dispatch", headers=admin_headers)

    call_id = client.get(f"/calls?campaign_id={campaign_id}", headers=admin_headers).json()[0]["id"]
    response = client.post(
        f"/calls/{call_id}/disposition",
        headers=admin_headers,
        json={
            "disposition": "QUALIFIED_HOT",
            "summary": "Owns house, bill 7500, wants site survey",
            "ai_payload": {"monthly_bill": 7500, "lead_score": 92},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["disposition"] == "QUALIFIED_HOT"
    assert body["ai_payload"]["lead_score"] == 92

    with SessionLocal() as db:
        assert db.query(CampaignLead).one().status is CampaignLeadStatus.COMPLETED

    stats = client.get(f"/campaigns/{campaign_id}", headers=admin_headers).json()["stats"]
    assert stats["dispositions"]["QUALIFIED_HOT"] == 1
    assert stats["total_calls"] == 1


def test_callback_request_overrides_retry_rules(client, admin_headers, operator_headers):
    upload_id = upload_leads(client, operator_headers, [("A", "9876543210")])
    campaign_id = make_campaign(client, admin_headers)["id"]
    client.post(
        f"/campaigns/{campaign_id}/leads", headers=admin_headers, json={"upload_id": upload_id}
    )
    client.post(f"/campaigns/{campaign_id}/start", headers=admin_headers)
    client.post(f"/campaigns/{campaign_id}/dispatch", headers=admin_headers)

    call_id = client.get("/calls", headers=admin_headers).json()[0]["id"]
    callback_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    client.post(
        f"/calls/{call_id}/disposition",
        headers=admin_headers,
        json={
            "disposition": "CALLBACK_REQUESTED",
            "callback_at": callback_at.isoformat(),
        },
    )

    with SessionLocal() as db:
        entry = db.query(CampaignLead).one()
        assert entry.status is CampaignLeadStatus.PENDING
        assert entry.next_attempt_at is not None
        # Scheduled for the customer's time, not the NO_ANSWER retry delay
        assert abs((entry.next_attempt_at.replace(tzinfo=dt.timezone.utc) - callback_at)
                   .total_seconds()) < 2

    # Not due yet, so the dispatcher leaves it alone
    assert (
        client.post(f"/campaigns/{campaign_id}/dispatch", headers=admin_headers).json()[
            "calls_placed"
        ]
        == 0
    )


def test_do_not_call_suppresses_immediately(client, admin_headers, operator_headers):
    upload_id = upload_leads(client, operator_headers, [("A", "9876543210")])
    campaign_id = make_campaign(client, admin_headers)["id"]
    client.post(
        f"/campaigns/{campaign_id}/leads", headers=admin_headers, json={"upload_id": upload_id}
    )
    client.post(f"/campaigns/{campaign_id}/start", headers=admin_headers)
    client.post(f"/campaigns/{campaign_id}/dispatch", headers=admin_headers)

    call_id = client.get("/calls", headers=admin_headers).json()[0]["id"]
    client.post(
        f"/calls/{call_id}/disposition",
        headers=admin_headers,
        json={"disposition": "DO_NOT_CALL"},
    )

    opt_outs = client.get("/compliance/opt-outs", headers=admin_headers).json()
    assert [entry["phone"] for entry in opt_outs] == ["+919876543210"]

    customers = client.get("/customers?phone=9876543210", headers=admin_headers).json()
    assert customers[0]["opted_out"] is True

    # A later upload of the same number is blocked
    upload = upload_leads(client, operator_headers, [("A", "9876543210")])
    summary = client.get(f"/leads/uploads/{upload}", headers=admin_headers).json()
    assert summary["opted_out_count"] == 1


def test_dispatcher_skips_leads_suppressed_after_queueing(
    client, admin_headers, operator_headers
):
    upload_id = upload_leads(client, operator_headers, [("A", "9876543210")])
    campaign_id = make_campaign(client, admin_headers)["id"]
    client.post(
        f"/campaigns/{campaign_id}/leads", headers=admin_headers, json={"upload_id": upload_id}
    )
    client.post(f"/campaigns/{campaign_id}/start", headers=admin_headers)

    # Customer opts out through another channel after being queued
    client.post("/compliance/opt-outs", headers=admin_headers, json={"phone": "9876543210"})

    assert (
        client.post(f"/campaigns/{campaign_id}/dispatch", headers=admin_headers).json()[
            "calls_placed"
        ]
        == 0
    )
    assert get_telephony_provider().placed_calls == []
    with SessionLocal() as db:
        assert db.query(CampaignLead).one().status is CampaignLeadStatus.SUPPRESSED


def test_webhook_requires_token_when_configured(client, admin_headers, operator_headers, monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "telephony_webhook_token", "s3cret")
    response = client.post("/calls/webhooks/mock", json={"call_id": "x", "status": "completed"})
    assert response.status_code == 401

    response = client.post(
        "/calls/webhooks/mock?token=s3cret", json={"call_id": "x", "status": "completed"}
    )
    assert response.status_code == 200  # unknown call, but authenticated


def test_webhook_for_unknown_call_is_acknowledged(client):
    response = client.post(
        "/calls/webhooks/mock", json={"call_id": "does-not-exist", "status": "completed"}
    )
    assert response.status_code == 200
    assert response.json() == {"received": True, "call_id": None}


def test_stopping_campaign_releases_pending_leads(client, admin_headers, operator_headers):
    upload_id = upload_leads(client, operator_headers, [("A", "9876543210")])
    campaign_id = make_campaign(client, admin_headers)["id"]
    client.post(
        f"/campaigns/{campaign_id}/leads", headers=admin_headers, json={"upload_id": upload_id}
    )
    client.post(f"/campaigns/{campaign_id}/start", headers=admin_headers)
    client.post(f"/campaigns/{campaign_id}/stop", headers=admin_headers)

    with SessionLocal() as db:
        assert db.query(CampaignLead).one().status is CampaignLeadStatus.CANCELLED
        assert db.query(Lead).one().status is LeadStatus.READY


@pytest.mark.parametrize(
    "window,now_ist,expected",
    [
        (("10:00", "18:00"), "2026-09-09 11:30", True),
        (("10:00", "18:00"), "2026-09-09 09:59", False),
        (("10:00", "18:00"), "2026-09-09 18:00", False),
        (("22:00", "06:00"), "2026-09-09 23:30", True),  # wraps midnight
        (("22:00", "06:00"), "2026-09-09 12:00", False),
    ],
)
def test_calling_window(window, now_ist, expected):
    from zoneinfo import ZoneInfo

    start, end = (dt.time.fromisoformat(value) for value in window)
    campaign = Campaign(
        name="w",
        status=CampaignStatus.RUNNING,
        window_start=start,
        window_end=end,
        timezone="Asia/Kolkata",
        created_by_id=1,
    )
    now = dt.datetime.fromisoformat(now_ist).replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    assert is_within_calling_window(campaign, now) is expected


def test_dispatch_blocked_outside_calling_window(client, admin_headers, operator_headers):
    upload_id = upload_leads(client, operator_headers, [("A", "9876543210")])
    # A one-minute window that has already passed today in IST
    campaign_id = make_campaign(
        client, admin_headers, window_start="00:00:00", window_end="00:01:00"
    )["id"]
    client.post(
        f"/campaigns/{campaign_id}/leads", headers=admin_headers, json={"upload_id": upload_id}
    )
    client.post(f"/campaigns/{campaign_id}/start", headers=admin_headers)

    now_ist = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).time()
    if now_ist < dt.time(0, 1):
        pytest.skip("Test run happens to fall inside the configured window")

    assert (
        client.post(f"/campaigns/{campaign_id}/dispatch", headers=admin_headers).json()[
            "calls_placed"
        ]
        == 0
    )
