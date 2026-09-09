"""AI provider abstractions (MVP section 10).

The conversation orchestrator talks only to these interfaces, so speech,
language and voice vendors can each be swapped without touching business
logic:

    SpeechProvider   transcribe() / detect_language()
    LLMProvider      generate() / extract()
    VoiceProvider    synthesize()
"""

import enum
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, Field

from backend.leads.models import ServiceType


class Intent(str, enum.Enum):
    """What the customer wants to happen next."""

    CONTINUE = "CONTINUE"  # keep qualifying
    QUALIFIED = "QUALIFIED"  # enough information gathered
    SITE_SURVEY_REQUESTED = "SITE_SURVEY_REQUESTED"
    CALLBACK_REQUESTED = "CALLBACK_REQUESTED"
    HUMAN_REQUEST = "HUMAN_REQUEST"
    NOT_INTERESTED = "NOT_INTERESTED"
    OPT_OUT = "OPT_OUT"  # "do not call me again"
    EXISTING_CUSTOMER = "EXISTING_CUSTOMER"
    SERVICE_REQUEST = "SERVICE_REQUEST"
    WRONG_NUMBER = "WRONG_NUMBER"


#: Intents that end the conversation.
TERMINAL_INTENTS = frozenset(
    {
        Intent.QUALIFIED,
        Intent.SITE_SURVEY_REQUESTED,
        Intent.CALLBACK_REQUESTED,
        Intent.HUMAN_REQUEST,
        Intent.NOT_INTERESTED,
        Intent.OPT_OUT,
        Intent.EXISTING_CUSTOMER,
        Intent.SERVICE_REQUEST,
        Intent.WRONG_NUMBER,
    }
)


class TurnDecision(BaseModel):
    """Structured decision the LLM returns for each conversation turn.

    The reply is what the customer hears; everything else drives the
    qualification state, routing and lead record.
    """

    reply: str = Field(
        description=(
            "What to say next, in the customer's language. One or two short "
            "spoken sentences — this is read aloud on a phone call."
        )
    )
    language: str = Field(
        default="te-IN",
        description="BCP-47 code of the language used in the reply, e.g. te-IN or en-IN.",
    )
    service: ServiceType | None = Field(
        default=None, description="Swaraj service the customer needs, once identifiable."
    )
    intent: Intent = Field(default=Intent.CONTINUE, description="What should happen next.")
    extracted: dict = Field(
        default_factory=dict,
        description=(
            "Qualification fields learned in this turn only, as flat key/value "
            "pairs. Never guess a value the customer did not state."
        ),
    )
    callback_at: str | None = Field(
        default=None,
        description="ISO-8601 time the customer asked to be called back, if they named one.",
    )


@dataclass
class Transcription:
    text: str
    language: str = "te-IN"
    confidence: float | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class Speech:
    """Synthesized audio ready to be played down the call."""

    audio: bytes
    mime_type: str = "audio/mpeg"
    voice_id: str | None = None
    cached: bool = False


class AIProviderError(Exception):
    """Raised when a speech, language or voice provider fails."""


class SpeechProvider(Protocol):
    name: str

    def transcribe(self, audio: bytes, *, language: str | None = None) -> Transcription:
        """Convert customer audio to text."""

    def detect_language(self, audio: bytes) -> str:
        """Return the BCP-47 code of the language being spoken."""


class LLMProvider(Protocol):
    name: str

    def generate(self, *, system: str, messages: list[dict]) -> TurnDecision:
        """Decide what to say next and what was learned."""

    def extract(self, *, system: str, transcript: str, schema: type[BaseModel]) -> BaseModel:
        """Pull structured data out of a completed conversation."""


class VoiceProvider(Protocol):
    name: str

    def synthesize(self, text: str, *, language: str = "te-IN") -> Speech:
        """Convert the reply text to speech."""
