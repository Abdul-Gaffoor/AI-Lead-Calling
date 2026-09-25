"""Exotel provider.

Endpoint, parameters and callback fields follow Exotel's published Call APIs:

    POST https://<subdomain>/v1/Accounts/<sid>/Calls/connect.json
    From, To, CallerId, Url, CustomField, StatusCallback,
    StatusCallbackEvents, StatusCallbackContentType

**Still confirm this against the account's own documentation before the first
real campaign.** Exotel provisions different products (Connect / Campaigns /
SIP media streaming) and the contract varies by account — in particular the
callback field names and whether live transfer is available at all. Until that
is done `TELEPHONY_PROVIDER=mock` remains the default and no real calls are
placed.

Credentials come from settings, never from code:
    EXOTEL_SID, EXOTEL_API_KEY, EXOTEL_API_TOKEN,
    EXOTEL_CALLER_ID, EXOTEL_SUBDOMAIN, EXOTEL_FLOW_APP_ID
"""

import logging
import time

import httpx

from backend.core.config import settings
from backend.telephony.base import (
    CallRequest,
    CallResult,
    CallState,
    TelephonyError,
    WebhookEvent,
)

logger = logging.getLogger(__name__)

# Exotel's call status values, mapped onto our own states. Keys are lowercased
# before lookup, so capitalisation from the provider does not matter.
_STATE_MAP = {
    "queued": CallState.QUEUED,
    "ringing": CallState.RINGING,
    "in-progress": CallState.IN_PROGRESS,
    "in_progress": CallState.IN_PROGRESS,
    "inprogress": CallState.IN_PROGRESS,
    "answered": CallState.IN_PROGRESS,
    "completed": CallState.COMPLETED,
    "complete": CallState.COMPLETED,
    "no-answer": CallState.NO_ANSWER,
    "no_answer": CallState.NO_ANSWER,
    "noanswer": CallState.NO_ANSWER,
    "busy": CallState.BUSY,
    "failed": CallState.FAILED,
    "canceled": CallState.FAILED,
    "cancelled": CallState.FAILED,
}

#: Statuses Exotel may send under any of these keys, in order of preference.
_STATUS_KEYS = ("Status", "CallStatus", "DialCallStatus")

#: Likewise for the call identifier and the duration.
_CALL_ID_KEYS = ("CallSid", "Sid", "CallId")
_DURATION_KEYS = ("DialCallDuration", "ConversationDuration", "Duration")

#: HTTP statuses worth retrying: rate limiting and Exotel-side faults.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _first(payload: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _as_seconds(value: str | None) -> int | None:
    """Duration in whole seconds. Exotel has been seen to send "42" and "42.0"."""
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


class ExotelProvider:
    name = "exotel"

    def __init__(self, timeout: float | None = None) -> None:
        missing = [
            key
            for key, value in (
                ("EXOTEL_SID", settings.exotel_sid),
                ("EXOTEL_API_KEY", settings.exotel_api_key),
                ("EXOTEL_API_TOKEN", settings.exotel_api_token),
                ("EXOTEL_CALLER_ID", settings.exotel_caller_id),
                # Without a flow the request has no destination: Exotel needs
                # either a Url (the AI voicebot applet) or a second number to
                # bridge to. Failing here beats a rejected call per lead.
                ("EXOTEL_FLOW_APP_ID", settings.exotel_flow_app_id),
            )
            if not value
        ]
        if missing:
            raise TelephonyError(
                "Exotel is not configured; missing: " + ", ".join(missing)
            )
        self._base_url = (
            f"https://{settings.exotel_subdomain}/v1/Accounts/{settings.exotel_sid}"
        )
        self._auth = (settings.exotel_api_key, settings.exotel_api_token)
        self._timeout = timeout if timeout is not None else settings.exotel_timeout_seconds

    # --- HTTP ---------------------------------------------------------------

    def _post(self, path: str, data: dict) -> dict:
        """POST to Exotel, retrying transient failures.

        Exotel puts the useful part of a rejection in the response body
        ("CallerId not found", "insufficient balance"), so it is carried into
        the error rather than discarded — otherwise every failure reads as a
        bare HTTP status.
        """
        url = f"{self._base_url}{path}"
        attempts = max(1, settings.exotel_max_retries + 1)
        last_error: str = "no attempt was made"

        for attempt in range(attempts):
            try:
                response = httpx.post(
                    url, data=data, auth=self._auth, timeout=self._timeout
                )
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code < 400:
                    try:
                        return response.json()
                    except ValueError:
                        raise TelephonyError(
                            "Exotel returned a non-JSON response "
                            f"({response.status_code}): {response.text[:300]}"
                        ) from None

                detail = response.text[:300].replace("\n", " ").strip()
                last_error = f"HTTP {response.status_code}: {detail}"
                if response.status_code not in _RETRYABLE_STATUS:
                    break

            if attempt + 1 < attempts:
                delay = 2**attempt
                logger.warning(
                    "Exotel request to %s failed (%s); retrying in %ss", path, last_error, delay
                )
                time.sleep(delay)

        raise TelephonyError(f"Exotel request to {path} failed: {last_error}")

    # --- TelephonyProvider --------------------------------------------------

    def place_call(self, request: CallRequest) -> CallResult:
        payload = {
            # The customer's leg is dialled first; Exotel then connects the
            # answered call to the flow that fronts the AI agent.
            "From": request.to_number,
            "CallerId": request.from_number or settings.exotel_caller_id,
            "CustomField": request.call_reference,
            "Url": (
                f"http://my.exotel.com/{settings.exotel_sid}"
                f"/exoml/start_voice/{settings.exotel_flow_app_id}"
            ),
        }
        if request.status_callback_url:
            payload["StatusCallback"] = request.status_callback_url
            # Subscribe explicitly. The completion callback is what releases
            # the concurrency slot and records the outcome; without it a call
            # stays "in flight" until the stuck-call sweep reclaims it.
            if settings.exotel_status_callback_events:
                payload["StatusCallbackEvents"] = settings.exotel_status_callback_events
            if settings.exotel_status_callback_content_type:
                payload["StatusCallbackContentType"] = (
                    settings.exotel_status_callback_content_type
                )

        body = self._post("/Calls/connect.json", payload)
        call = body.get("Call") or {}
        provider_call_id = call.get("Sid")
        if not provider_call_id:
            raise TelephonyError(
                f"Exotel response did not include a call Sid: {str(body)[:300]}"
            )
        return CallResult(
            provider_call_id=str(provider_call_id),
            state=self._state_for(call.get("Status"), default=CallState.QUEUED),
            raw=body,
        )

    def transfer(self, provider_call_id: str, to_number: str) -> None:
        raise TelephonyError(
            "Live transfer must be configured on the Exotel call flow; "
            "confirm the account's transfer mechanism before use"
        )

    def hangup(self, provider_call_id: str) -> None:
        # Exotel's v1 Call APIs publish no hangup endpoint; the previous
        # implementation posted a Twilio-shaped request that this account would
        # reject. Saying so beats appearing to work.
        raise TelephonyError(
            "Exotel exposes no documented hangup endpoint on the v1 Call API; "
            "end the call from the call flow instead"
        )

    def parse_webhook(self, payload: dict) -> WebhookEvent:
        status = _first(payload, _STATUS_KEYS)
        return WebhookEvent(
            provider_call_id=_first(payload, _CALL_ID_KEYS) or "",
            state=self._state_for(status, default=CallState.FAILED),
            call_reference=payload.get("CustomField"),
            duration_seconds=_as_seconds(_first(payload, _DURATION_KEYS)),
            recording_url=payload.get("RecordingUrl"),
            raw=payload,
        )

    # --- helpers ------------------------------------------------------------

    def _state_for(self, status, *, default: CallState) -> CallState:
        """Map an Exotel status, logging anything unrecognised.

        A status we do not know about is treated as `default` rather than
        guessed at — but it is logged loudly, because silently mapping an
        unfamiliar value is how a working call gets recorded as a failure.
        """
        if status in (None, ""):
            return default
        mapped = _STATE_MAP.get(str(status).strip().lower())
        if mapped is None:
            logger.warning(
                "Unrecognised Exotel call status %r; treating it as %s. "
                "Add it to _STATE_MAP in backend/telephony/exotel.py.",
                status,
                default.value,
            )
            return default
        return mapped
