"""Voice activity detection and endpointing (MVP section 9).

Two questions the media bridge has to answer many times a second:

* Is the customer speaking right now? — that is barge-in. If they start
  talking while the AI is mid-sentence, playback stops.
* Have they finished? — that ends the turn and sends the transcript to the
  orchestrator.

This is an energy-based detector over 20 ms frames with hysteresis. It is
deliberately simple: no model, no dependency, no network call, and the same
answer every time for the same audio, which is what lets the media-bridge
tests be deterministic. A real deployment may well want WebRTC VAD or the
provider's own endpointing — `Endpointer` is small enough to swap.

The thresholds matter more than the algorithm. Telephony audio is noisy,
Indian call quality varies, and people pause mid-sentence to think. Cutting
somebody off mid-answer is worse than half a second of dead air, so the
silence window is generous by default and configurable.
"""

from __future__ import annotations

import array
import math
from dataclasses import dataclass, field
from enum import Enum

from backend.ai.streaming import FRAME_MS


class Activity(str, Enum):
    SILENCE = "SILENCE"
    SPEECH = "SPEECH"
    #: Speech just ended and the silence window has elapsed: the turn is over.
    ENDPOINT = "ENDPOINT"


def frame_energy(frame: bytes) -> float:
    """Root-mean-square amplitude of a 16-bit PCM frame, 0.0 to 1.0."""
    if len(frame) < 2:
        return 0.0
    samples = array.array("h")
    # An odd trailing byte is a truncated sample; drop it rather than
    # misreading the frame by one byte and getting noise.
    samples.frombytes(frame[: len(frame) - (len(frame) % 2)])
    if not samples:
        return 0.0
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    return math.sqrt(mean_square) / 32768.0


@dataclass
class Endpointer:
    """Tracks whether the customer is speaking, and when they stopped.

    Hysteresis in both directions: a single loud frame is not speech (a
    door closing), and a single quiet frame is not the end of a turn (the
    gap between words).
    """

    #: RMS above which a frame counts as speech. 0.02 is roughly a quiet
    #: speaker on a poor line; below that is line noise.
    threshold: float = 0.02
    #: Consecutive speech frames before speech is declared — 60 ms. Short
    #: enough to catch barge-in, long enough to ignore a click.
    onset_frames: int = 3
    #: Silence before a turn is considered over — 700 ms. People pause
    #: mid-sentence; cutting them off is worse than waiting.
    silence_ms: int = 700
    #: A turn cannot run forever, however long somebody talks.
    max_utterance_ms: int = 30_000

    speaking: bool = False
    _speech_run: int = 0
    _silence_run: int = 0
    _utterance_ms: int = 0
    _total_ms: int = 0
    _history: list[bool] = field(default_factory=list, repr=False)

    @property
    def silence_frames(self) -> int:
        return max(1, self.silence_ms // FRAME_MS)

    @property
    def utterance_ms(self) -> int:
        """How long the current utterance has been running."""
        return self._utterance_ms

    @property
    def total_ms(self) -> int:
        """Audio seen since this endpointer was created."""
        return self._total_ms

    def push(self, frame: bytes) -> Activity:
        """Classify one frame and return what it means for the turn."""
        self._total_ms += FRAME_MS
        loud = frame_energy(frame) >= self.threshold
        self._history.append(loud)

        if loud:
            self._speech_run += 1
            self._silence_run = 0
            if not self.speaking and self._speech_run >= self.onset_frames:
                self.speaking = True
                # Count the frames that triggered onset, not just this one:
                # the first syllable is part of the utterance.
                self._utterance_ms = self._speech_run * FRAME_MS
            elif self.speaking:
                self._utterance_ms += FRAME_MS
            return Activity.SPEECH if self.speaking else Activity.SILENCE

        self._speech_run = 0
        if not self.speaking:
            return Activity.SILENCE

        # In an utterance, and this frame is quiet.
        self._silence_run += 1
        self._utterance_ms += FRAME_MS
        if self._silence_run >= self.silence_frames:
            self.reset_utterance()
            return Activity.ENDPOINT
        # A short gap between words is still part of the utterance.
        return Activity.SPEECH

    def exceeded_max_utterance(self) -> bool:
        return self.speaking and self._utterance_ms >= self.max_utterance_ms

    def reset_utterance(self) -> None:
        """Start listening for a fresh utterance."""
        self.speaking = False
        self._speech_run = 0
        self._silence_run = 0
        self._utterance_ms = 0
