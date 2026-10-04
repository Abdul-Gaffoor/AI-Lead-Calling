"""The real-time media bridge (MVP section 9).

This is the piece that was missing: the thing that carries live call audio
into the conversation orchestrator and speech back out.

    telephony  ──frames──▶  Endpointer ──▶ SpeechSession ──final text──┐
       ▲                        │                                      │
       │                   barge-in                                    ▼
       └──audio chunks──  StreamingVoice  ◀──reply──  conversation.handle_turn

`MediaSession` holds the state for one call and is deliberately free of
any transport: it takes frames in and hands audio out, and knows nothing
about WebSockets, Exotel or FastAPI. That is what lets the tests drive a
whole call as a list of byte strings, with no network and no provider
account, and it is what will let a SIP trunk replace Exotel later without
touching the conversation logic.

The orchestrator is called exactly as the turn-based API calls it, with the
final transcript of each utterance. Everything `CLAUDE.md` calls a product
requirement therefore still holds on a live call: the disclosure is still
scripted and spoken first, opt-out is still matched deterministically on
the transcript, and every figure still comes from the solar engine.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy.orm import Session

from backend.ai.conversation import handle_turn, start_conversation
from backend.ai.factory import get_streaming_speech_provider, get_streaming_voice_provider
from backend.ai.models import Conversation, ConversationState
from backend.ai.streaming import FRAME_MS, AudioChunk, TranscriptEvent
from backend.ai.vad import Activity, Endpointer
from backend.calls.models import CallAttempt
from backend.core.config import settings

logger = logging.getLogger(__name__)


class Phase(str, Enum):
    """What the bridge is doing with the line right now."""

    SPEAKING = "SPEAKING"    # playing the AI's reply
    LISTENING = "LISTENING"  # waiting for or hearing the customer
    THINKING = "THINKING"    # transcript in, reply not ready
    ENDED = "ENDED"


@dataclass
class TurnRecord:
    """What one exchange cost, for the MVP section 38 latency measure."""

    transcript: str
    reply: str
    #: Wall-clock from end of customer speech to first audio chunk out.
    first_audio_ms: int
    #: Wall-clock for the whole turn.
    total_ms: int


@dataclass
class MediaSession:
    """One live call.

    Transport-free by design: `push` takes audio frames and returns audio
    frames. A WebSocket handler, a SIP bridge or a test all drive it the
    same way.
    """

    db: Session
    conversation: Conversation
    endpointer: Endpointer
    phase: Phase = Phase.SPEAKING
    turns: list[TurnRecord] = field(default_factory=list)
    #: Set when the orchestrator ends the call, so the transport knows to
    #: play out the final line and hang up rather than keep listening.
    finished: bool = False

    _speech = None
    _preroll: list[bytes] = field(default_factory=list)
    _interrupted: bool = False
    _silence_ms: int = 0

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def begin(cls, db: Session, attempt: CallAttempt) -> tuple["MediaSession", Iterator[AudioChunk]]:
        """Open a call and return the disclosure, already being spoken.

        The disclosure is the scripted opening line (rule 2), not a model
        output, and it goes out before the customer has said anything.
        """
        conversation, _ = start_conversation(db, attempt)
        session = cls(
            db=db,
            conversation=conversation,
            endpointer=Endpointer(
                threshold=settings.vad_threshold,
                silence_ms=settings.vad_silence_ms,
                max_utterance_ms=settings.max_utterance_ms,
            ),
        )
        session._speech = get_streaming_speech_provider().open(
            language=conversation.language
        )
        greeting = conversation.turns[0].text if conversation.turns else ""
        return session, session._speak(greeting)

    def close(self) -> None:
        self.phase = Phase.ENDED
        if self._speech is not None:
            try:
                self._speech.close()
            except Exception:  # a provider failing to close must not fail the call
                logger.warning("Speech session did not close cleanly", exc_info=True)
            self._speech = None

    # -- the audio path ----------------------------------------------------

    def push(self, frame: bytes) -> list[AudioChunk]:
        """Feed 20 ms of customer audio; get back whatever to play.

        Returns an empty list most of the time. The bridge only speaks at
        the end of a customer's utterance, or when it is interrupted.
        """
        if self.phase is Phase.ENDED:
            return []

        was_speaking = self.endpointer.speaking
        activity = self.endpointer.push(frame)

        # Keep the last few frames even while the line sounds idle. The
        # endpointer needs several loud frames before it calls something
        # speech, and without this the first syllable of every answer —
        # the 60 ms that carries "వద్దు" versus "ఔను" — never reaches the
        # transcriber.
        self._preroll.append(frame)
        if len(self._preroll) > self.endpointer.onset_frames:
            self._preroll.pop(0)

        if activity is Activity.SILENCE:
            self._silence_ms += FRAME_MS
            if self._silence_ms >= settings.media_idle_timeout_s * 1000:
                logger.info("Conversation %s idle; closing", self.conversation.id)
                self.close()
            return []
        self._silence_ms = 0

        # Barge-in: the customer started talking over the AI. Stop playing
        # immediately — talking over a customer is the single most
        # obviously robotic thing a voice agent can do.
        if not was_speaking and self.endpointer.speaking and self.phase is Phase.SPEAKING:
            self._interrupted = True
            self.phase = Phase.LISTENING
            logger.debug("Barge-in on conversation %s", self.conversation.id)

        events: list[TranscriptEvent] = []
        if self._speech is not None:
            if not was_speaking and self.endpointer.speaking:
                # Utterance just started: hand over the buffered onset
                # frames in order, this one included.
                for buffered in self._preroll:
                    events += self._speech.push(buffered)
                self._preroll.clear()
            else:
                events += self._speech.push(frame)

        # An utterance that runs too long has to be cut somewhere, or a
        # customer who never pauses holds the line open forever.
        if self.endpointer.exceeded_max_utterance():
            logger.info("Utterance limit reached on conversation %s", self.conversation.id)
            self.endpointer.reset_utterance()
            return self._complete_turn()

        if activity is Activity.ENDPOINT:
            return self._complete_turn()

        self._note_partials(events)
        return []

    def _note_partials(self, events: list[TranscriptEvent]) -> None:
        """Partials are for barge-in and for latency bookkeeping only.

        Nothing is acted on until a final transcript: deciding an intent
        from half a sentence is how "don't" becomes "do".
        """
        for event in events:
            if event.is_final:
                logger.debug("Unexpected final transcript outside endpoint handling")

    def _complete_turn(self) -> list[AudioChunk]:
        """The customer stopped talking: transcribe, decide, reply."""
        started = time.monotonic()
        self.phase = Phase.THINKING

        finals = self._speech.flush() if self._speech else []
        transcript = " ".join(e.text for e in finals if e.text).strip()
        if not transcript:
            # Silence that the VAD read as an utterance. Say nothing rather
            # than asking the model to respond to nothing.
            self.phase = Phase.LISTENING
            return []

        language = next((e.language for e in finals if e.language), None)
        if language:
            self.conversation.language = language

        decision, _ = handle_turn(self.db, self.conversation, text=transcript)

        first_audio = int((time.monotonic() - started) * 1000)
        chunks = list(self._speak(decision.reply))
        self.turns.append(
            TurnRecord(
                transcript=transcript,
                reply=decision.reply,
                first_audio_ms=first_audio,
                total_ms=int((time.monotonic() - started) * 1000),
            )
        )

        if self.conversation.state is ConversationState.ENDED:
            # Play the closing line, then the transport hangs up.
            self.finished = True
        return chunks

    def _speak(self, text: str) -> Iterator[AudioChunk]:
        """Synthesise a reply, stopping early if the customer interrupts."""
        self.phase = Phase.SPEAKING
        self._interrupted = False
        if not text:
            return iter(())

        voice = get_streaming_voice_provider()
        chunks: list[AudioChunk] = []
        for chunk in voice.stream(text, language=self.conversation.language):
            if self._interrupted:
                logger.debug("Dropping remaining audio after barge-in")
                break
            chunks.append(chunk)
        return iter(chunks)

    # -- what the transport needs to know ----------------------------------

    @property
    def should_hang_up(self) -> bool:
        return self.finished or self.phase is Phase.ENDED

    def latency_summary(self) -> dict:
        """First-audio latency across the call (MVP section 38)."""
        if not self.turns:
            return {"turns": 0, "first_audio_ms_p50": None, "first_audio_ms_max": None}
        values = sorted(turn.first_audio_ms for turn in self.turns)
        return {
            "turns": len(values),
            "first_audio_ms_p50": values[len(values) // 2],
            "first_audio_ms_max": values[-1],
        }
