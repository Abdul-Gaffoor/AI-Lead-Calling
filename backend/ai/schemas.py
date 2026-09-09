import base64
import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.ai.base import Intent
from backend.ai.models import ConversationState, Speaker
from backend.leads.models import ServiceType


class ConversationStart(BaseModel):
    call_id: int


class TurnRequest(BaseModel):
    """What the customer said — as text, or as base64 audio to transcribe."""

    text: str | None = None
    audio_base64: str | None = None

    @model_validator(mode="after")
    def check_one_input(self):
        if (self.text is None) == (self.audio_base64 is None):
            raise ValueError("Provide exactly one of text or audio_base64")
        return self

    def audio_bytes(self) -> bytes | None:
        if self.audio_base64 is None:
            return None
        try:
            return base64.b64decode(self.audio_base64, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("audio_base64 is not valid base64") from exc


class TurnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    speaker: Speaker
    text: str
    language: str | None
    latency_ms: int | None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    call_attempt_id: int
    lead_id: int | None
    customer_id: int
    state: ConversationState
    language: str
    service: ServiceType | None
    collected: dict
    turn_count: int
    summary: str | None
    final_intent: str | None
    started_at: dt.datetime
    ended_at: dt.datetime | None


class ConversationDetail(ConversationOut):
    turns: list[TurnOut] = Field(default_factory=list)
    structured_output: dict = Field(default_factory=dict)


class ReplyOut(BaseModel):
    """The AI's response for this turn."""

    conversation_id: int
    reply: str
    language: str
    intent: Intent
    service: ServiceType | None = None
    state: ConversationState
    collected: dict = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    #: Base64 audio of the reply; empty when synthesis is unavailable.
    audio_base64: str = ""
    audio_mime_type: str = "audio/mpeg"
