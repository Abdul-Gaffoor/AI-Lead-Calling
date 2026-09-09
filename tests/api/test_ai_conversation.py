import base64
import io

import pytest

from backend.ai.factory import get_llm_provider, get_voice_provider, reset_ai_provider_cache
from backend.ai.models import Conversation, ConversationState
from backend.ai.prompts import build_system_prompt, opening_line
from backend.ai.qualification import is_sufficient, missing_fields
from backend.calls.models import CallAttempt, Disposition
from backend.core.database import SessionLocal
from backend.leads.models import ServiceType
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


@pytest.fixture
def answered_call(client, admin_headers):
    """A campaign with one dialled call, ready for a conversation."""
    csv = HEADERS + "Ramesh,9876543210,WEBSITE,YES,Miyapur,7500\n"
    upload = client.post(
        "/leads/uploads", headers=admin_headers,
        files={"file": ("leads.csv", io.BytesIO(csv.encode()), "text/csv")},
    ).json()
    campaign = client.post(
        "/campaigns", headers=admin_headers,
        json={"name": "AI test", "concurrency": 2, "max_attempts": 3, **ALL_DAY},
    ).json()
    client.post(
        f"/campaigns/{campaign['id']}/leads", headers=admin_headers,
        json={"upload_id": upload["id"]},
    )
    client.post(f"/campaigns/{campaign['id']}/start", headers=admin_headers)
    client.post(f"/campaigns/{campaign['id']}/dispatch", headers=admin_headers)
    return client.get("/calls", headers=admin_headers).json()[0]


def begin(client, headers, call):
    response = client.post(
        "/ai/conversations", headers=headers, json={"call_id": call["id"]}
    )
    assert response.status_code == 201, response.text
    return response.json()


def say(client, headers, conversation_id, text):
    response = client.post(
        f"/ai/conversations/{conversation_id}/turn", headers=headers, json={"text": text}
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- AI disclosure (MVP section 8) -----------------------------------------


def test_call_opens_with_ai_disclosure(client, admin_headers, answered_call):
    body = begin(client, admin_headers, answered_call)
    assert "AI virtual assistant" in body["reply"]
    assert "Swaraj Solar" in body["reply"]
    assert "Ramesh" in body["reply"]
    assert body["state"] == "GREETING"
    # Audio is returned for the telephony layer to play
    assert base64.b64decode(body["audio_base64"]).decode() == body["reply"]


def test_disclosure_is_scripted_not_model_generated(client, admin_headers, answered_call):
    """The disclosure must not depend on the LLM, so it can never be skipped."""
    begin(client, admin_headers, answered_call)
    assert get_llm_provider().calls == []


@pytest.mark.parametrize("language,expected", [("te-IN", "AI virtual assistant"), ("en-IN", "AI virtual assistant")])
def test_opening_line_in_both_languages(language, expected):
    assert expected in opening_line("Ramesh Kumar", language)
    assert "Ramesh" in opening_line("Ramesh Kumar", language)


def test_second_conversation_on_same_call_is_rejected(client, admin_headers, answered_call):
    begin(client, admin_headers, answered_call)
    response = client.post(
        "/ai/conversations", headers=admin_headers, json={"call_id": answered_call["id"]}
    )
    assert response.status_code == 409


# --- Qualification flow -----------------------------------------------------


def test_conversation_gathers_fields_and_seeds_from_lead(client, admin_headers, answered_call):
    body = begin(client, admin_headers, answered_call)
    cid = body["conversation_id"]
    # The lead already told us the bill and city — pre-seeded, not re-asked
    assert body["collected"]["monthly_bill"] == 7500.0
    assert body["collected"]["location"] == "Miyapur"

    body = say(client, admin_headers, cid, "Yes I own my house and roof is free")
    assert body["state"] == "QUALIFYING"
    assert body["collected"]["property_owned"] is True
    assert body["collected"]["roof_available"] is True

    detail = client.get(f"/ai/conversations/{cid}", headers=admin_headers).json()
    speakers = [turn["speaker"] for turn in detail["turns"]]
    assert speakers == ["AI", "CUSTOMER", "AI"]


def test_lead_context_is_given_to_the_model(client, admin_headers, answered_call):
    cid = begin(client, admin_headers, answered_call)["conversation_id"]
    say(client, admin_headers, cid, "tell me more")
    context = get_llm_provider().calls[0][0]["content"]
    assert "Ramesh" in context
    assert "Miyapur" in context
    assert "7500" in context


# --- Outcomes map to Sprint 2 dispositions ---------------------------------


@pytest.mark.parametrize(
    "utterance,intent,disposition",
    [
        ("I want a site survey", "SITE_SURVEY_REQUESTED", "SITE_SURVEY_REQUESTED"),
        ("I want to talk to a sales person", "HUMAN_REQUEST", "HUMAN_TRANSFER"),
        ("not interested", "NOT_INTERESTED", "NOT_INTERESTED"),
        ("this is a wrong number", "WRONG_NUMBER", "WRONG_NUMBER"),
        ("I already have solar installed", "EXISTING_CUSTOMER", "EXISTING_CUSTOMER"),
        ("my panels need cleaning", "SERVICE_REQUEST", "SERVICE_REQUEST"),
    ],
)
def test_intent_sets_call_disposition(
    client, admin_headers, answered_call, utterance, intent, disposition
):
    cid = begin(client, admin_headers, answered_call)["conversation_id"]
    body = say(client, admin_headers, cid, utterance)
    assert body["intent"] == intent
    assert body["state"] == "ENDED"

    call = client.get(f"/calls/{answered_call['id']}", headers=admin_headers).json()
    assert call["disposition"] == disposition
    assert call["summary"]
    assert call["ai_payload"]["intent"] == intent


def test_opt_out_suppresses_the_number(client, admin_headers, answered_call):
    cid = begin(client, admin_headers, answered_call)["conversation_id"]
    body = say(client, admin_headers, cid, "please don't call me again")
    assert body["intent"] == "OPT_OUT"

    call = client.get(f"/calls/{answered_call['id']}", headers=admin_headers).json()
    assert call["disposition"] == "DO_NOT_CALL"

    opt_outs = client.get("/compliance/opt-outs", headers=admin_headers).json()
    assert [entry["phone"] for entry in opt_outs] == ["+919876543210"]


def test_telugu_opt_out_is_detected(client, admin_headers, answered_call):
    cid = begin(client, admin_headers, answered_call)["conversation_id"]
    body = say(client, admin_headers, cid, "నాకు మళ్లీ call చేయకండి")
    assert body["intent"] == "OPT_OUT"
    assert body["language"] == "te-IN"


def test_opt_out_overrides_the_model(client, admin_headers, answered_call, monkeypatch):
    """A stop-calling request must not depend on the model noticing it."""
    from backend.ai.base import Intent, TurnDecision

    provider = get_llm_provider()
    monkeypatch.setattr(
        provider,
        "generate",
        lambda **kwargs: TurnDecision(reply="Tell me more!", intent=Intent.CONTINUE),
    )
    cid = begin(client, admin_headers, answered_call)["conversation_id"]
    body = say(client, admin_headers, cid, "do not call me again")
    assert body["intent"] == "OPT_OUT"
    call = client.get(f"/calls/{answered_call['id']}", headers=admin_headers).json()
    assert call["disposition"] == "DO_NOT_CALL"


def test_callback_request_carries_the_time_through(client, admin_headers, answered_call):
    cid = begin(client, admin_headers, answered_call)["conversation_id"]
    body = say(client, admin_headers, cid, "call me tomorrow at 11")
    assert body["intent"] == "CALLBACK_REQUESTED"

    from backend.campaigns.models import CampaignLead, CampaignLeadStatus

    with SessionLocal() as db:
        entry = db.query(CampaignLead).one()
        assert entry.status is CampaignLeadStatus.PENDING
        assert entry.customer_requested_at is not None


# --- Safety rails -----------------------------------------------------------


def test_llm_failure_falls_back_to_human_not_dead_air(
    client, admin_headers, answered_call, monkeypatch
):
    from backend.ai.base import AIProviderError

    provider = get_llm_provider()

    def explode(**kwargs):
        raise AIProviderError("provider is down")

    monkeypatch.setattr(provider, "generate", explode)
    cid = begin(client, admin_headers, answered_call)["conversation_id"]
    body = say(client, admin_headers, cid, "hello")

    assert body["intent"] == "HUMAN_REQUEST"
    assert body["reply"]  # the customer hears something, not silence
    call = client.get(f"/calls/{answered_call['id']}", headers=admin_headers).json()
    assert call["disposition"] == "HUMAN_TRANSFER"


def test_conversation_cannot_run_forever(client, admin_headers, answered_call, monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "max_conversation_turns", 3)
    cid = begin(client, admin_headers, answered_call)["conversation_id"]
    for _ in range(3):
        body = say(client, admin_headers, cid, "hmm ok")
    assert body["state"] == "ENDED"

    response = client.post(
        f"/ai/conversations/{cid}/turn", headers=admin_headers, json={"text": "hello?"}
    )
    assert response.status_code == 409


def test_turn_requires_exactly_one_input(client, admin_headers, answered_call):
    cid = begin(client, admin_headers, answered_call)["conversation_id"]
    for payload in ({}, {"text": "hi", "audio_base64": "aGk="}):
        response = client.post(
            f"/ai/conversations/{cid}/turn", headers=admin_headers, json=payload
        )
        assert response.status_code == 422


def test_audio_turn_is_transcribed(client, admin_headers, answered_call):
    cid = begin(client, admin_headers, answered_call)["conversation_id"]
    audio = base64.b64encode("I want a site survey".encode()).decode()
    response = client.post(
        f"/ai/conversations/{cid}/turn", headers=admin_headers, json={"audio_base64": audio}
    )
    assert response.status_code == 200
    assert response.json()["intent"] == "SITE_SURVEY_REQUESTED"

    detail = client.get(f"/ai/conversations/{cid}", headers=admin_headers).json()
    assert detail["turns"][1]["text"] == "I want a site survey"


def test_conversation_requires_authentication(client, answered_call):
    assert client.post("/ai/conversations", json={"call_id": answered_call["id"]}).status_code == 401


# --- The prompt itself ------------------------------------------------------


def test_system_prompt_forbids_the_model_doing_solar_maths():
    """MVP section 19: the approved solar engine calculates, never the LLM."""
    prompt = build_system_prompt().lower()
    assert "never calculate" in prompt
    for forbidden in ("savings", "subsidy", "payback", "cost"):
        assert forbidden in prompt
    assert "never invent" in prompt


def test_system_prompt_covers_every_service_type():
    prompt = build_system_prompt()
    for service in ServiceType:
        assert service.value in prompt


# --- Qualification helpers --------------------------------------------------


def test_missing_and_sufficient_fields():
    collected = {"location": "Miyapur", "property_owned": True, "monthly_bill": 7500}
    remaining = missing_fields(ServiceType.RESIDENTIAL_SOLAR, collected)
    assert "roof_available" in remaining
    assert "location" not in remaining
    assert not is_sufficient(ServiceType.RESIDENTIAL_SOLAR, collected)

    full = dict.fromkeys(
        ["location", "property_owned", "property_type", "monthly_bill",
         "roof_available", "installation_timeline", "site_survey"], "yes"
    )
    assert is_sufficient(ServiceType.RESIDENTIAL_SOLAR, full)
    assert not is_sufficient(None, full)


def test_structured_output_has_no_invented_score(client, admin_headers, answered_call):
    """Lead score arrives in Sprint 6 — it must not be guessed here."""
    cid = begin(client, admin_headers, answered_call)["conversation_id"]
    say(client, admin_headers, cid, "I want a site survey")
    detail = client.get(f"/ai/conversations/{cid}", headers=admin_headers).json()
    payload = detail["structured_output"]
    assert payload["service"] == "RESIDENTIAL_SOLAR"
    assert payload["intent"] == "SITE_SURVEY_REQUESTED"
    assert "lead_score" not in payload
    assert "classification" not in payload
