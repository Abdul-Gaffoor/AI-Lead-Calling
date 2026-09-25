"""The Exotel provider (MVP sections 5, 10).

This is the code that will place real calls to real customers and spend real
money, so it is exercised here without touching the network: httpx.post is
replaced and the provider is driven against recorded-shaped responses.

What it cannot prove is that Exotel's contract matches — endpoints and callback
field names vary by provisioned product, which is why the module says to
confirm against the account's own documentation before the first campaign.
"""

import httpx
import pytest

from backend.telephony.base import CallRequest, CallState, TelephonyError
from backend.telephony.factory import reset_provider_cache

CREDS = {
    "exotel_sid": "swaraj1",
    "exotel_api_key": "key",
    "exotel_api_token": "token",
    "exotel_caller_id": "08047164423",
    "exotel_flow_app_id": "99001",
    "exotel_subdomain": "api.exotel.com",
}

CONNECT_OK = {
    "Call": {
        "Sid": "abc123",
        "Status": "queued",
        "From": "+919876543210",
        "To": "08047164423",
    }
}


@pytest.fixture(autouse=True)
def exotel_configured(monkeypatch):
    for key, value in CREDS.items():
        monkeypatch.setattr(f"backend.core.config.settings.{key}", value)
    # Retries sleep; nothing here should wait on a real clock.
    monkeypatch.setattr("backend.telephony.exotel.time.sleep", lambda _s: None)
    reset_provider_cache()
    yield
    reset_provider_cache()


def provider():
    from backend.telephony.exotel import ExotelProvider

    return ExotelProvider()


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json = json_body
        self.text = text if text else (str(json_body) if json_body else "")

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


def capture(monkeypatch, *responses):
    """Replace httpx.post; return the list that records each call's kwargs."""
    calls = []
    queue = list(responses)

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return queue.pop(0) if queue else FakeResponse(200, CONNECT_OK)

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


# --- configuration ----------------------------------------------------------


def test_missing_credentials_are_named(monkeypatch):
    monkeypatch.setattr("backend.core.config.settings.exotel_api_token", "")
    monkeypatch.setattr("backend.core.config.settings.exotel_caller_id", "")

    with pytest.raises(TelephonyError) as exc:
        provider()
    assert "EXOTEL_API_TOKEN" in str(exc.value)
    assert "EXOTEL_CALLER_ID" in str(exc.value)


def test_a_call_with_no_destination_is_refused_up_front(monkeypatch):
    """Without a flow applet the request has no destination at all. Failing
    once at startup beats Exotel rejecting every lead in the campaign."""
    monkeypatch.setattr("backend.core.config.settings.exotel_flow_app_id", "")

    with pytest.raises(TelephonyError) as exc:
        provider()
    assert "EXOTEL_FLOW_APP_ID" in str(exc.value)


# --- placing a call ---------------------------------------------------------


def test_place_call_sends_the_documented_parameters(monkeypatch):
    calls = capture(monkeypatch, FakeResponse(200, CONNECT_OK))

    result = provider().place_call(
        CallRequest(
            to_number="+919876543210",
            call_reference="42",
            from_number="08047164423",
            status_callback_url="https://swaraj.example.com/calls/webhooks/exotel",
        )
    )

    assert result.provider_call_id == "abc123"
    assert result.state is CallState.QUEUED

    sent = calls[0]
    assert sent["url"] == "https://api.exotel.com/v1/Accounts/swaraj1/Calls/connect.json"
    assert sent["auth"] == ("key", "token")

    data = sent["data"]
    # The customer's leg is dialled first, then bridged to the AI flow.
    assert data["From"] == "+919876543210"
    assert data["CallerId"] == "08047164423"
    assert data["CustomField"] == "42"
    assert data["Url"] == "http://my.exotel.com/swaraj1/exoml/start_voice/99001"
    assert data["StatusCallback"] == "https://swaraj.example.com/calls/webhooks/exotel"


def test_the_completion_callback_is_subscribed_to(monkeypatch):
    """Without this the call never leaves an active state, and its concurrency
    slot is held until the stuck-call sweep reclaims it."""
    calls = capture(monkeypatch, FakeResponse(200, CONNECT_OK))

    provider().place_call(
        CallRequest(
            to_number="+919876543210",
            call_reference="1",
            status_callback_url="https://swaraj.example.com/hook",
        )
    )

    assert calls[0]["data"]["StatusCallbackEvents"] == "terminal"
    assert calls[0]["data"]["StatusCallbackContentType"] == "application/json"


def test_exotels_rejection_reason_reaches_the_operator(monkeypatch):
    """"HTTP 400" alone is useless; the body says "CallerId not found"."""
    capture(
        monkeypatch,
        FakeResponse(400, None, text='{"RestException":{"Message":"CallerId not found"}}'),
    )

    with pytest.raises(TelephonyError) as exc:
        provider().place_call(CallRequest(to_number="+919876543210", call_reference="1"))

    assert "400" in str(exc.value)
    assert "CallerId not found" in str(exc.value)


def test_a_transient_failure_is_retried_rather_than_burning_an_attempt(monkeypatch):
    """A dropped connection should not cost the customer one of three attempts."""
    calls = capture(
        monkeypatch,
        FakeResponse(503, None, text="upstream busy"),
        FakeResponse(200, CONNECT_OK),
    )

    result = provider().place_call(
        CallRequest(to_number="+919876543210", call_reference="1")
    )

    assert result.provider_call_id == "abc123"
    assert len(calls) == 2, "the 503 should have been retried"


def test_a_rejection_is_not_retried(monkeypatch):
    """A bad CallerId will be just as bad the second time."""
    calls = capture(monkeypatch, FakeResponse(401, None, text="unauthorised"))

    with pytest.raises(TelephonyError):
        provider().place_call(CallRequest(to_number="+919876543210", call_reference="1"))

    assert len(calls) == 1


def test_a_network_error_is_retried_then_reported(monkeypatch):
    attempts = []

    def fake_post(url, **kwargs):
        attempts.append(url)
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(TelephonyError) as exc:
        provider().place_call(CallRequest(to_number="+919876543210", call_reference="1"))

    assert len(attempts) == 3  # one try plus two retries
    assert "ConnectTimeout" in str(exc.value)


def test_a_response_without_a_sid_is_an_error(monkeypatch):
    capture(monkeypatch, FakeResponse(200, {"Call": {}}))

    with pytest.raises(TelephonyError) as exc:
        provider().place_call(CallRequest(to_number="+919876543210", call_reference="1"))
    assert "Sid" in str(exc.value)


# --- status callbacks -------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        ("completed", CallState.COMPLETED),
        ("Completed", CallState.COMPLETED),
        ("no-answer", CallState.NO_ANSWER),
        ("busy", CallState.BUSY),
        ("failed", CallState.FAILED),
        ("canceled", CallState.FAILED),
        ("in-progress", CallState.IN_PROGRESS),
        ("ringing", CallState.RINGING),
    ],
)
def test_every_documented_status_maps(status, expected):
    event = provider().parse_webhook({"CallSid": "abc", "Status": status})
    assert event.state is expected


def test_the_callback_is_read_whatever_key_names_arrive():
    """Exotel's field names vary by product, so the common aliases are all
    accepted rather than silently producing an event with no call id."""
    event = provider().parse_webhook(
        {
            "CallStatus": "completed",
            "Sid": "xyz789",
            "ConversationDuration": "42",
            "CustomField": "7",
            "RecordingUrl": "https://recordings.exotel.com/x.mp3",
        }
    )

    assert event.provider_call_id == "xyz789"
    assert event.state is CallState.COMPLETED
    assert event.duration_seconds == 42
    assert event.call_reference == "7"
    assert event.recording_url == "https://recordings.exotel.com/x.mp3"


def test_a_fractional_duration_is_accepted():
    """"42.0" used to parse as no duration at all, losing the call length."""
    event = provider().parse_webhook(
        {"CallSid": "a", "Status": "completed", "DialCallDuration": "42.0"}
    )
    assert event.duration_seconds == 42


def test_a_missing_duration_is_none():
    event = provider().parse_webhook({"CallSid": "a", "Status": "completed"})
    assert event.duration_seconds is None


def test_an_unrecognised_status_is_logged(caplog):
    """Silently mapping an unfamiliar status is how a working call gets
    recorded as a failure with nobody the wiser."""
    with caplog.at_level("WARNING"):
        event = provider().parse_webhook({"CallSid": "a", "Status": "carrier-congestion"})

    assert event.state is CallState.FAILED
    assert "carrier-congestion" in caplog.text


# --- interface honesty ------------------------------------------------------


def test_transfer_and_hangup_say_they_are_unavailable():
    """Both used to look implemented. hangup posted a Twilio-shaped request
    that Exotel has no endpoint for."""
    for call in (
        lambda: provider().transfer("abc", "+919999999999"),
        lambda: provider().hangup("abc"),
    ):
        with pytest.raises(TelephonyError):
            call()
