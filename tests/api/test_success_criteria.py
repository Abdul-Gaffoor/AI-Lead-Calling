"""The MVP §38 success criteria (reports/success.py).

§38 opens with "Don't judge the project by 'AI sounds impressive'". These
tests mostly check that the module refuses to produce an impressive number
it cannot support.
"""

import io

import pytest

from backend.calls.models import CallAttempt, Disposition
from backend.core.database import SessionLocal
from backend.reports.success import success_criteria

HEADERS = "Customer_Name,Mobile_Number,Lead_Source,Consent_Status,City,Monthly_Bill\n"
ALL_DAY = {"window_start": "00:00:00", "window_end": "23:59:59"}


def _measure(payload, name):
    return next(m for m in payload["measures"] if m["name"] == name)


def test_an_empty_database_reports_no_data_not_zero():
    """
    "0% qualification rate" reads as failure. It means "no calls yet",
    which is a different thing, and a dashboard must not confuse them.
    """
    with SessionLocal() as db:
        payload = success_criteria(db)

    for name in ("Call connection rate", "Qualification rate", "AI response latency"):
        measure = _measure(payload, name)
        assert measure["value"] is None, f"{name} invented a number from no data"
        assert measure["unavailable"], f"{name} did not say why it is missing"


def test_every_section_38_measure_is_accounted_for():
    """
    §38 lists eleven. Eight come from call records and three from the
    evaluation harness; none may simply be absent.
    """
    with SessionLocal() as db:
        payload = success_criteria(db)

    produced = {m["name"] for m in payload["measures"]}
    produced |= set(payload["from_evaluation_harness"]["measures"])

    for expected in (
        "Telugu understanding accuracy",
        "Required-field capture accuracy",
        "Lead-classification accuracy",
        "Qualification rate",
        "AI response latency",
        "Call connection rate",
        "Human escalation accuracy",
        "Site-survey conversion",
        "Cost per qualified lead",
        "Human calling hours saved",
        "Customer opt-out rate",
        "Average call duration",
    ):
        assert expected in produced, f"§38 asks for {expected!r} and nothing reports it"


def test_rates_are_computed_from_dispositions(client, admin_headers):
    csv = HEADERS + "".join(
        f"Lead{i},98765432{i:02d},WEBSITE,YES,Miyapur,7500\n" for i in range(4)
    )
    upload = client.post(
        "/leads/uploads", headers=admin_headers,
        files={"file": ("leads.csv", io.BytesIO(csv.encode()), "text/csv")},
    ).json()
    campaign = client.post(
        "/campaigns", headers=admin_headers,
        json={"name": "Metrics", "concurrency": 4, "max_attempts": 1, **ALL_DAY},
    ).json()
    client.post(
        f"/campaigns/{campaign['id']}/leads", headers=admin_headers,
        json={"upload_id": upload["id"]},
    )
    client.post(f"/campaigns/{campaign['id']}/start", headers=admin_headers)
    client.post(f"/campaigns/{campaign['id']}/dispatch", headers=admin_headers)

    with SessionLocal() as db:
        attempts = db.scalars(__import__("sqlalchemy").select(CallAttempt)).all()
        # Two connected and qualified, one opted out, one never answered.
        attempts[0].disposition = Disposition.QUALIFIED_HOT
        attempts[0].duration_seconds = 120
        attempts[1].disposition = Disposition.SITE_SURVEY_REQUESTED
        attempts[1].duration_seconds = 180
        attempts[2].disposition = Disposition.DO_NOT_CALL
        attempts[2].duration_seconds = 30
        attempts[3].disposition = Disposition.NO_ANSWER
        db.commit()

        payload = success_criteria(db)

    assert payload["totals"]["attempted"] == 4
    assert payload["totals"]["connected"] == 3
    assert payload["totals"]["qualified"] == 2
    assert _measure(payload, "Call connection rate")["value"] == 75.0
    assert _measure(payload, "Qualification rate")["value"] == pytest.approx(66.7, abs=0.1)
    # Opt-out is measured against conversations, not dials.
    assert _measure(payload, "Customer opt-out rate")["value"] == pytest.approx(33.3, abs=0.1)


def test_derived_figures_carry_their_assumptions():
    """
    A cost figure with no provenance gets quoted in a board meeting. Same
    treatment as the solar engine's constants.
    """
    with SessionLocal() as db:
        payload = success_criteria(db)

    for name in ("Cost per qualified lead", "Human calling hours saved"):
        assumptions = _measure(payload, name)["assumptions"]
        assert assumptions, f"{name} reported nothing about where it came from"
        assert "PLACEHOLDER" in assumptions["status"]


def test_cost_needs_rates_to_be_configured(monkeypatch):
    from backend.core import config

    monkeypatch.setattr(config.settings, "telephony_cost_per_minute", 0.0)
    monkeypatch.setattr(config.settings, "ai_cost_per_minute", 0.0)

    with SessionLocal() as db:
        measure = _measure(success_criteria(db), "Cost per qualified lead")
    assert measure["value"] is None
    assert measure["unavailable"]


def test_the_human_comparison_is_left_empty_on_purpose():
    """
    §38 asks to compare against the current human process. Inventing that
    baseline produces exactly the impressive-looking number §38 warns
    about, so it stays null until somebody measures it.
    """
    with SessionLocal() as db:
        comparison = success_criteria(db)["comparison"]
    assert comparison["human_process"] is None
    assert "measured from Swaraj" in comparison["note"]


def test_the_endpoint_requires_authentication(client):
    assert client.get("/reports/success-criteria").status_code == 401


def test_the_endpoint_returns_the_report(client, admin_headers):
    response = client.get("/reports/success-criteria", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert "measures" in response.json()
