"""In-memory telephony provider.

Used for tests and for running the platform before a cloud-telephony
account/virtual number is provisioned. It records every request instead of
dialling anyone, so no real calls are ever placed.
"""

import uuid

from backend.telephony.base import (
    CallRequest,
    CallResult,
    CallState,
    WebhookEvent,
)

_STATE_MAP = {
    "queued": CallState.QUEUED,
    "ringing": CallState.RINGING,
    "in-progress": CallState.IN_PROGRESS,
    "answered": CallState.IN_PROGRESS,
    "completed": CallState.COMPLETED,
    "no-answer": CallState.NO_ANSWER,
    "busy": CallState.BUSY,
    "failed": CallState.FAILED,
}


class MockTelephonyProvider:
    name = "mock"

    def __init__(self) -> None:
        self.placed_calls: list[CallRequest] = []
        self.transfers: list[tuple[str, str]] = []
        self.hangups: list[str] = []

    def place_call(self, request: CallRequest) -> CallResult:
        self.placed_calls.append(request)
        return CallResult(
            provider_call_id=f"mock-{uuid.uuid4().hex[:16]}",
            state=CallState.QUEUED,
            raw={"to": request.to_number, "reference": request.call_reference},
        )

    def transfer(self, provider_call_id: str, to_number: str) -> None:
        self.transfers.append((provider_call_id, to_number))

    def hangup(self, provider_call_id: str) -> None:
        self.hangups.append(provider_call_id)

    def parse_webhook(self, payload: dict) -> WebhookEvent:
        raw_state = str(payload.get("status", "")).lower()
        duration = payload.get("duration")
        return WebhookEvent(
            provider_call_id=str(payload.get("call_id", "")),
            state=_STATE_MAP.get(raw_state, CallState.FAILED),
            call_reference=payload.get("reference"),
            duration_seconds=int(duration) if duration not in (None, "") else None,
            recording_url=payload.get("recording_url"),
            raw=payload,
        )
