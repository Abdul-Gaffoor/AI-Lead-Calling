"""Exotel provider.

IMPORTANT: the endpoint paths, request fields and callback field names below
follow Exotel's published Call APIs, but they MUST be confirmed against the
account's own API documentation and approved calling configuration during
onboarding — Exotel provisions different products (Connect / Campaigns / SIP
media streaming) and the exact contract varies by account. Until that is
verified and credentials are configured, `TELEPHONY_PROVIDER=mock` is the
default so no real calls are placed.

Credentials come from settings (never hard-code them):
    EXOTEL_SID, EXOTEL_API_KEY, EXOTEL_API_TOKEN,
    EXOTEL_CALLER_ID, EXOTEL_SUBDOMAIN
"""

import httpx

from backend.core.config import settings
from backend.telephony.base import (
    CallRequest,
    CallResult,
    CallState,
    TelephonyError,
    WebhookEvent,
)

# Exotel reports call status with these values; map them onto our states.
_STATE_MAP = {
    "queued": CallState.QUEUED,
    "ringing": CallState.RINGING,
    "in-progress": CallState.IN_PROGRESS,
    "in_progress": CallState.IN_PROGRESS,
    "completed": CallState.COMPLETED,
    "no-answer": CallState.NO_ANSWER,
    "no_answer": CallState.NO_ANSWER,
    "busy": CallState.BUSY,
    "failed": CallState.FAILED,
    "canceled": CallState.FAILED,
    "cancelled": CallState.FAILED,
}


class ExotelProvider:
    name = "exotel"

    def __init__(self, timeout: float = 20.0) -> None:
        missing = [
            key
            for key, value in (
                ("EXOTEL_SID", settings.exotel_sid),
                ("EXOTEL_API_KEY", settings.exotel_api_key),
                ("EXOTEL_API_TOKEN", settings.exotel_api_token),
                ("EXOTEL_CALLER_ID", settings.exotel_caller_id),
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
        self._timeout = timeout

    def _post(self, path: str, data: dict) -> dict:
        try:
            response = httpx.post(
                f"{self._base_url}{path}",
                data=data,
                auth=self._auth,
                timeout=self._timeout,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise TelephonyError(f"Exotel request failed: {exc}") from exc
        except ValueError as exc:
            raise TelephonyError("Exotel returned a non-JSON response") from exc

    def place_call(self, request: CallRequest) -> CallResult:
        payload = {
            "From": request.to_number,  # customer leg is dialled first
            "CallerId": request.from_number or settings.exotel_caller_id,
            "CustomField": request.call_reference,
        }
        if request.status_callback_url:
            payload["StatusCallback"] = request.status_callback_url
        if settings.exotel_flow_app_id:
            # Connect the answered customer to the AI voicebot flow/applet.
            payload["Url"] = (
                f"http://my.exotel.com/{settings.exotel_sid}"
                f"/exoml/start_voice/{settings.exotel_flow_app_id}"
            )

        body = self._post("/Calls/connect.json", payload)
        call = body.get("Call") or {}
        provider_call_id = call.get("Sid")
        if not provider_call_id:
            raise TelephonyError("Exotel response did not include a call Sid")
        return CallResult(
            provider_call_id=str(provider_call_id),
            state=_STATE_MAP.get(str(call.get("Status", "")).lower(), CallState.QUEUED),
            raw=body,
        )

    def transfer(self, provider_call_id: str, to_number: str) -> None:
        # Live transfer is configured on the call flow in Exotel; this hook is
        # here so the interface is complete. Confirm the mechanism for the
        # account's product before relying on it in production.
        raise TelephonyError(
            "Live transfer must be configured on the Exotel call flow; "
            "confirm the account's transfer mechanism before use"
        )

    def hangup(self, provider_call_id: str) -> None:
        self._post(f"/Calls/{provider_call_id}.json", {"Status": "completed"})

    def parse_webhook(self, payload: dict) -> WebhookEvent:
        duration = payload.get("DialCallDuration") or payload.get("ConversationDuration")
        status = str(
            payload.get("Status") or payload.get("DialCallStatus") or ""
        ).lower()
        return WebhookEvent(
            provider_call_id=str(payload.get("CallSid") or payload.get("Sid") or ""),
            state=_STATE_MAP.get(status, CallState.FAILED),
            call_reference=payload.get("CustomField"),
            duration_seconds=int(duration) if str(duration or "").isdigit() else None,
            recording_url=payload.get("RecordingUrl"),
            raw=payload,
        )
