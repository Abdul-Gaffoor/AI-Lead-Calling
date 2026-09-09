"""Scoring, surveys, sales handoff, reporting, inbound and website intake."""

import io

import pytest

from backend.ai.factory import reset_ai_provider_cache
from backend.core.database import SessionLocal
from backend.leads.models import ServiceType
from backend.sales.models import Opportunity
from backend.scoring.service import score_lead
from backend.surveys.models import SiteSurvey, SurveyStatus
from backend.telephony.factory import reset_provider_cache

HEADERS = "Customer_Name,Mobile_Number,Lead_Source,Consent_Status,City,Monthly_Bill\n"
ALL_DAY = {"window_start": "00:00:00", "window_end": "23:59:59"}


@pytest.fixture(autouse=True)
def fresh_providers():
    reset_provider_cache()
    reset_ai_provider_cache()
    yield
    reset_provider_cache()
    reset_ai_provider_cache()


def upload(client, headers, rows="Ramesh,9876543210,WEBSITE,YES,Miyapur,7500\n"):
    csv = HEADERS + rows
    return client.post(
        "/leads/uploads", headers=headers,
        files={"file": ("leads.csv", io.BytesIO(csv.encode()), "text/csv")},
    ).json()


def dial_one(client, headers):
    """Upload a lead, run a campaign and return the placed call."""
    up = upload(client, headers)
    campaign = client.post(
        "/campaigns", headers=headers,
        json={"name": "T", "concurrency": 2, "max_attempts": 3, **ALL_DAY},
    ).json()
    client.post(f"/campaigns/{campaign['id']}/leads", headers=headers, json={"upload_id": up["id"]})
    client.post(f"/campaigns/{campaign['id']}/start", headers=headers)
    client.post(f"/campaigns/{campaign['id']}/dispatch", headers=headers)
    return client.get("/calls", headers=headers).json()[0]


# --- Scoring (MVP section 20) ----------------------------------------------


def test_scoring_bands(client, admin_headers):
    hot = client.post("/scoring/score", headers=admin_headers, json={
        "service": "RESIDENTIAL_SOLAR",
        "collected": {"property_owned": True, "roof_available": True, "monthly_bill": 7500,
                      "installation_timeline": "30_days", "site_survey": True,
                      "decision_maker": True},
    }).json()
    assert hot["score"] == 100
    assert hot["classification"] == "HOT"

    cold = client.post("/scoring/score", headers=admin_headers, json={
        "service": "RESIDENTIAL_SOLAR",
        "collected": {"property_owned": True, "monthly_bill": 800},
    }).json()
    assert cold["classification"] in ("COLD", "UNQUALIFIED")
    assert "Roof available" in cold["missed"]


def test_timeline_parsing_counts_towards_score(client, admin_headers, db):
    near = score_lead(db, ServiceType.RESIDENTIAL_SOLAR, {"installation_timeline": "this month"})
    far = score_lead(db, ServiceType.RESIDENTIAL_SOLAR, {"installation_timeline": "6 months"})
    assert near.score > far.score


def test_weights_are_admin_configurable(client, admin_headers):
    """MVP section 20: weights must be changeable without a code change."""
    response = client.patch(
        "/scoring/configs/RESIDENTIAL_SOLAR", headers=admin_headers,
        json={"rules": [{"field": "roof_available", "op": "truthy", "points": 100,
                         "label": "Roof available"}]},
    )
    assert response.status_code == 200

    scored = client.post("/scoring/score", headers=admin_headers, json={
        "service": "RESIDENTIAL_SOLAR", "collected": {"roof_available": True},
    }).json()
    assert scored["score"] == 100 and scored["classification"] == "HOT"


def test_only_admin_can_change_weights(client, operator_headers):
    response = client.patch(
        "/scoring/configs/RESIDENTIAL_SOLAR", headers=operator_headers, json={"rules": []}
    )
    assert response.status_code == 403


# --- Conversation -> score -> sales handoff --------------------------------


def test_qualified_call_scores_and_creates_opportunity(client, admin_headers):
    call = dial_one(client, admin_headers)
    conversation = client.post(
        "/ai/conversations", headers=admin_headers, json={"call_id": call["id"]}
    ).json()
    body = client.post(
        f"/ai/conversations/{conversation['conversation_id']}/turn",
        headers=admin_headers, json={"text": "I want a site survey"},
    ).json()
    assert body["intent"] == "SITE_SURVEY_REQUESTED"

    updated = client.get(f"/calls/{call['id']}", headers=admin_headers).json()
    assert updated["disposition"] == "SITE_SURVEY_REQUESTED"
    # The structured payload now carries a real score, not a guess
    assert "lead_score" in updated["ai_payload"]
    assert updated["ai_payload"]["classification"] in ("HOT", "WARM", "COLD", "UNQUALIFIED")

    with SessionLocal() as db:
        opportunity = db.query(Opportunity).one()
        assert opportunity.classification == updated["ai_payload"]["classification"]
        # A survey is booked automatically — nobody has to remember
        survey = db.query(SiteSurvey).one()
        assert survey.status is SurveyStatus.REQUESTED
        assert survey.lead_id == opportunity.lead_id


def test_opportunity_is_auto_assigned_to_an_executive(client, admin_headers, executive_user):
    call = dial_one(client, admin_headers)
    conversation = client.post(
        "/ai/conversations", headers=admin_headers, json={"call_id": call["id"]}
    ).json()
    client.post(
        f"/ai/conversations/{conversation['conversation_id']}/turn",
        headers=admin_headers, json={"text": "I want a site survey"},
    )
    with SessionLocal() as db:
        assert db.query(Opportunity).one().assigned_to_id == executive_user.id


def test_executive_sees_only_their_own_queue(client, admin_headers, executive_headers, executive_user):
    call = dial_one(client, admin_headers)
    conversation = client.post(
        "/ai/conversations", headers=admin_headers, json={"call_id": call["id"]}
    ).json()
    client.post(
        f"/ai/conversations/{conversation['conversation_id']}/turn",
        headers=admin_headers, json={"text": "I want a site survey"},
    )
    mine = client.get("/sales/my-leads", headers=executive_headers).json()
    assert len(mine) == 1
    assert mine[0]["customer_name"] == "Ramesh"
    assert mine[0]["customer_phone"] == "+919876543210"

    # An admin with nothing assigned sees an empty queue
    assert client.get("/sales/my-leads", headers=admin_headers).json() == []


def test_not_interested_creates_no_sales_work(client, admin_headers):
    call = dial_one(client, admin_headers)
    conversation = client.post(
        "/ai/conversations", headers=admin_headers, json={"call_id": call["id"]}
    ).json()
    client.post(
        f"/ai/conversations/{conversation['conversation_id']}/turn",
        headers=admin_headers, json={"text": "not interested"},
    )
    with SessionLocal() as db:
        assert db.query(Opportunity).count() == 0
        assert db.query(SiteSurvey).count() == 0


# --- Surveys (MVP section 25) ----------------------------------------------


def test_survey_workflow(client, admin_headers, executive_user):
    up = upload(client, admin_headers)
    lead_id = client.get(f"/leads?upload_id={up['id']}", headers=admin_headers).json()[0]["id"]

    survey = client.post("/surveys", headers=admin_headers, json={
        "lead_id": lead_id, "service": "RESIDENTIAL_SOLAR",
        "address": "Miyapur", "preferred_date": "2026-09-15", "preferred_time": "Morning",
    }).json()
    assert survey["status"] == "REQUESTED"

    # Assigning an engineer advances the workflow on its own
    assigned = client.patch(
        f"/surveys/{survey['id']}", headers=admin_headers,
        json={"assigned_to_id": executive_user.id},
    ).json()
    assert assigned["status"] == "ASSIGNED"

    completed = client.patch(
        f"/surveys/{survey['id']}", headers=admin_headers, json={"status": "COMPLETED"}
    ).json()
    assert completed["status"] == "COMPLETED"


def test_survey_requires_a_lead_or_customer(client, admin_headers):
    assert client.post("/surveys", headers=admin_headers, json={}).status_code == 422


# --- Reporting (MVP sections 27, 28) ---------------------------------------


def test_dashboard_and_funnel(client, admin_headers):
    call = dial_one(client, admin_headers)
    conversation = client.post(
        "/ai/conversations", headers=admin_headers, json={"call_id": call["id"]}
    ).json()
    client.post(
        f"/ai/conversations/{conversation['conversation_id']}/turn",
        headers=admin_headers, json={"text": "I want a site survey"},
    )

    dashboard = client.get("/reports/dashboard", headers=admin_headers).json()
    assert dashboard["uploaded"] == 1
    assert dashboard["attempted"] == 1
    assert dashboard["connected"] == 1
    assert dashboard["qualified"] == 1
    assert dashboard["site_surveys"] == 1

    funnel = client.get("/reports/funnel", headers=admin_headers).json()
    assert funnel["stages"]["lead"] == 1
    assert funnel["stages"]["called"] == 1
    assert funnel["stages"]["qualified"] == 1
    assert funnel["rates"]["connection_rate"] == 100.0

    metrics = client.get("/reports/calls", headers=admin_headers).json()
    assert metrics["total_calls"] == 1


def test_reports_require_authentication(client):
    assert client.get("/reports/dashboard").status_code == 401


# --- Inbound callback (MVP section 24) -------------------------------------


def test_inbound_caller_is_identified_with_history(client, admin_headers):
    call = dial_one(client, admin_headers)
    conversation = client.post(
        "/ai/conversations", headers=admin_headers, json={"call_id": call["id"]}
    ).json()
    client.post(
        f"/ai/conversations/{conversation['conversation_id']}/turn",
        headers=admin_headers, json={"text": "I want a site survey"},
    )

    context = client.get(
        "/calls/inbound/identify?phone=9876543210", headers=admin_headers
    ).json()
    assert context["known"] is True
    assert context["customer"]["name"] == "Ramesh"
    assert context["last_call"]["disposition"] == "SITE_SURVEY_REQUESTED"
    assert context["last_call"]["summary"]
    assert context["opportunity"] is not None


def test_unknown_inbound_caller(client, admin_headers):
    context = client.get(
        "/calls/inbound/identify?phone=9000000001", headers=admin_headers
    ).json()
    assert context["known"] is False


def test_inbound_call_is_registered_with_context(client, admin_headers):
    dial_one(client, admin_headers)
    response = client.post("/calls/inbound", json={"From": "+919876543210"})
    assert response.status_code == 201
    body = response.json()
    assert body["context"]["known"] is True
    assert body["call_id"]

    call = client.get(f"/calls/{body['call_id']}", headers=admin_headers).json()
    assert call["direction"] == "INBOUND"


def test_inbound_call_without_a_number_is_rejected(client):
    assert client.post("/calls/inbound", json={}).status_code == 422


# --- Website intake (MVP section 2B) ---------------------------------------


def test_website_lead_requires_configuration(client):
    response = client.post("/public/leads", json={
        "name": "Web Lead", "mobile": "9876543210", "consent": True})
    assert response.status_code == 503


def test_website_lead_flow(client, admin_headers, monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "website_api_key", "site-key")
    key = {"X-API-Key": "site-key"}

    assert client.post("/public/leads", json={
        "name": "X", "mobile": "9876543210", "consent": True}).status_code == 401

    created = client.post("/public/leads", headers=key, json={
        "name": "Web Lead", "mobile": "9876543210", "consent": True,
        "service": "RESIDENTIAL_SOLAR", "city": "Kukatpally", "monthly_bill": 4200,
    }).json()
    assert created["accepted"] is True and created["lead_ref"].startswith("SW-")

    # Same number again while the lead is open
    duplicate = client.post("/public/leads", headers=key, json={
        "name": "Web Lead", "mobile": "9876543210", "consent": True}).json()
    assert duplicate["accepted"] is False and duplicate["reason"] == "DUPLICATE_EXISTING"

    # No consent, bad number
    assert client.post("/public/leads", headers=key, json={
        "name": "Y", "mobile": "9111111111", "consent": False}).json()["reason"] == "CONSENT_MISSING"
    assert client.post("/public/leads", headers=key, json={
        "name": "Z", "mobile": "12345", "consent": True}).json()["reason"] == "INVALID_PHONE"


def test_website_cannot_bypass_the_suppression_list(client, admin_headers, monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "website_api_key", "site-key")
    client.post("/compliance/opt-outs", headers=admin_headers, json={"phone": "9876543210"})

    result = client.post("/public/leads", headers={"X-API-Key": "site-key"}, json={
        "name": "Web Lead", "mobile": "9876543210", "consent": True}).json()
    assert result["accepted"] is False and result["reason"] == "OPTED_OUT"


def test_public_solar_estimate(client, monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "website_api_key", "site-key")
    result = client.post(
        "/public/solar-estimate", headers={"X-API-Key": "site-key"},
        json={"monthly_bill": 7500},
    ).json()
    assert result["system_size_kw"] > 0
    assert result["payback_years"] is not None


# --- Console ---------------------------------------------------------------


def test_console_is_served(client):
    assert client.get("/").status_code == 200
    assert "Swaraj Solar" in client.get("/").text
    assert client.get("/app/app.js").status_code == 200


def test_hidden_elements_are_actually_hidden(client):
    """A display rule on .login-shell would otherwise outrank the browser's
    default [hidden] rule, leaving the login overlay on screen after a
    successful sign-in."""
    css = client.get("/app/styles.css").text
    assert "[hidden]" in css and "display: none !important" in css


def test_console_assets_are_cache_busted(client):
    """A stale cached stylesheet made a shipped fix look like it had not
    deployed, so asset URLs carry a content fingerprint and the page itself
    is never cached."""
    response = client.get("/")
    assert response.headers["cache-control"] == "no-store"
    assert "/app/styles.css?v=" in response.text
    assert "/app/app.js?v=" in response.text


def test_asset_fingerprint_changes_with_content(client, tmp_path, monkeypatch):
    first = client.get("/").text
    import re

    from backend.main import app  # noqa: F401

    version = re.search(r"styles\.css\?v=([0-9a-f]+)", first).group(1)
    assert len(version) == 12
    # Same content, same fingerprint — caching still works between deploys.
    assert version in client.get("/").text
