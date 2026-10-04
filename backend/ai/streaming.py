"""Streaming provider interfaces for real-time calls (MVP section 9).

The batch interfaces in `base.py` take a whole utterance and return a whole
reply. That is the right shape for a turn-based API and the wrong shape for
a phone call: the customer hears nothing until the slowest of STT, LLM and
TTS has finished, and there is no way to notice them interrupting.

These protocols sit beside the batch ones rather than replacing them. The
turn-based `/ai/conversations` API keeps working exactly as before; the
media bridge uses these. A provider may implement either or both, and
`factory.py` keeps the mocks as the default so none of this costs anything
until a real vendor is switched on.

Three things earn their complexity here:

* **Partial transcripts.** Needed to detect barge-in before the customer
  has finished their sentence, and to start thinking early.
* **Endpointing.** Knowing the customer has stopped talking is what ends a
  turn. A fixed timeout either cuts people off or leaves dead air.
* **Chunked speech.** Playing the first clause while the rest is still
  being synthesised is most of the perceived latency win.

What does NOT change: the orchestrator still sees a final text transcript
for every turn, so the deterministic opt-out match (rule 3), the solar
engine (rule 1) and the scripted disclosure (rule 2) all keep working.
A fully end-to-end speech-to-speech model would remove that checkpoint and
with it those guarantees, which is why this pipeline stays STT -> LLM -> TTS.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from backend.ai.base import TurnDecision

#: Telephony audio is 8 kHz, 16-bit, mono unless a provider says otherwise.
#: Frames are 20 ms, which is what both Exotel and a SIP trunk deliver.
DEFAULT_SAMPLE_RATE = 8_000
FRAME_MS = 20


@dataclass
class TranscriptEvent:
    """Something the speech provider heard.

    `is_final` separates a guess that may still change from a settled
    result. Only a final event may end a turn; a partial is for barge-in
    and for starting work early.
    """

    text: str
    is_final: bool = False
    language: str | None = None
    confidence: float | None = None
    #: Milliseconds of audio consumed when this event was produced. Used to
    #: measure the latency MVP section 38 asks for, against call time rather
    #: than wall-clock, so a slow test machine does not look like a slow AI.
    audio_ms: int = 0


@dataclass
class AudioChunk:
    """A piece of synthesised speech, ready to go down the call."""

    audio: bytes
    mime_type: str = "audio/pcm"
    sample_rate: int = DEFAULT_SAMPLE_RATE
    #: True on the last chunk, so the bridge knows the reply is complete
    #: rather than waiting for a timeout.
    final: bool = False


@runtime_checkable
class SpeechSession(Protocol):
    """One open speech-to-text stream, for the length of one call."""

    def push(self, frame: bytes) -> list[TranscriptEvent]:
        """Feed 20 ms of audio; return whatever the provider has decided."""

    def flush(self) -> list[TranscriptEvent]:
        """Force a final result — the customer has stopped speaking."""

    def close(self) -> None:
        """Release the provider's stream."""


@runtime_checkable
class StreamingSpeechProvider(Protocol):
    name: str

    def open(
        self, *, language: str | None = None, sample_rate: int = DEFAULT_SAMPLE_RATE
    ) -> SpeechSession:
        """Open a stream for one call."""


@runtime_checkable
class StreamingVoiceProvider(Protocol):
    name: str

    def stream(
        self, text: str, *, language: str = "te-IN", sample_rate: int = DEFAULT_SAMPLE_RATE
    ) -> Iterator[AudioChunk]:
        """Synthesise `text`, yielding audio as it becomes available."""


@dataclass
class ReplyStream:
    """An LLM reply arriving in pieces.

    Iterating yields text as the model produces it, so synthesis can start
    on the first clause. `decision` is only valid once iteration finishes —
    the structured fields (intent, extracted values, service) are what the
    orchestrator acts on, and acting on half of them would be worse than
    waiting.
    """

    chunks: Iterator[str]
    _decision: TurnDecision | None = None
    _text: list[str] = field(default_factory=list)
    _exhausted: bool = False

    def __iter__(self) -> Iterator[str]:
        for chunk in self.chunks:
            self._text.append(chunk)
            yield chunk
        self._exhausted = True

    @property
    def text(self) -> str:
        return "".join(self._text)

    def decision(self) -> TurnDecision:
        if not self._exhausted:
            raise RuntimeError(
                "The reply is still streaming. Finish iterating before "
                "reading the decision — the intent is not known until the "
                "model has finished."
            )
        if self._decision is None:
            raise RuntimeError("This stream produced no decision")
        return self._decision

    def set_decision(self, decision: TurnDecision) -> None:
        self._decision = decision


@runtime_checkable
class StreamingLLMProvider(Protocol):
    name: str

    def stream(self, *, system: str, messages: list[dict]) -> ReplyStream:
        """Decide what to say next, returning the reply as it is written."""


def sentences(text: str) -> Iterator[str]:
    """Split a reply into speakable pieces.

    Synthesising a whole paragraph before playing any of it wastes the time
    the customer spends listening to the first sentence. Splitting on
    sentence ends lets playback start sooner without chopping a clause in
    half, which is audible.

    Telugu uses the same full stop and question mark as English, plus the
    danda in older text.
    """
    buffer: list[str] = []
    for char in text:
        buffer.append(char)
        if char in ".?!।॥\n":
            piece = "".join(buffer).strip()
            if piece:
                yield piece
            buffer = []
    remainder = "".join(buffer).strip()
    if remainder:
        yield remainder
