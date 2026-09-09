"""Telephony provider abstraction (MVP section 10).

The application talks only to this interface, so the cloud-telephony vendor
(Exotel, Knowlarity, Ozonetel, Airtel IQ, ...) can be swapped without
rewriting the campaign or AI layers.
"""

import enum
from dataclasses import dataclass, field
from typing import Protocol


class CallState(str, enum.Enum):
    """Normalized call lifecycle, independent of any provider's vocabulary."""

    QUEUED = "QUEUED"
    RINGING = "RINGING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    NO_ANSWER = "NO_ANSWER"
    BUSY = "BUSY"
    FAILED = "FAILED"


#: States in which a call still occupies a concurrency slot.
ACTIVE_STATES = (CallState.QUEUED, CallState.RINGING, CallState.IN_PROGRESS)


@dataclass
class CallRequest:
    to_number: str
    call_reference: str
    from_number: str | None = None
    status_callback_url: str | None = None


@dataclass
class CallResult:
    provider_call_id: str
    state: CallState = CallState.QUEUED
    raw: dict = field(default_factory=dict)


@dataclass
class WebhookEvent:
    """A provider status callback, normalized."""

    provider_call_id: str
    state: CallState
    call_reference: str | None = None
    duration_seconds: int | None = None
    recording_url: str | None = None
    raw: dict = field(default_factory=dict)


class TelephonyError(Exception):
    """Raised when the provider rejects or fails a request."""


class TelephonyProvider(Protocol):
    name: str

    def place_call(self, request: CallRequest) -> CallResult:
        """Start an outbound call. Raises TelephonyError on failure."""

    def transfer(self, provider_call_id: str, to_number: str) -> None:
        """Transfer a live call to a human agent (MVP section 23)."""

    def hangup(self, provider_call_id: str) -> None:
        """End a live call."""

    def parse_webhook(self, payload: dict) -> WebhookEvent:
        """Translate a provider status callback into a WebhookEvent."""
