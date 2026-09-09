"""ElevenLabs speech providers.

Voice (text-to-speech) and, optionally, Scribe speech-to-text. Telugu is
covered by the multilingual models, but voice quality for Telugu MUST be
validated with native Telangana and Andhra speakers before production
(MVP section 37) — the provider is swappable precisely so that evaluation
can change the decision.

Endpoints follow ElevenLabs' documented REST API; confirm against the
account's current documentation when enabling.
"""

import logging

import httpx

from backend.ai.base import AIProviderError, Speech, Transcription
from backend.core.config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.elevenlabs.io/v1"


class ElevenLabsVoiceProvider:
    """Text-to-speech.

    Static lines (the AI disclosure greeting, standard closings) are
    synthesized once and cached in-process: they are identical on every
    call, so re-generating them wastes both latency and characters.
    """

    name = "elevenlabs"

    def __init__(self) -> None:
        if not settings.elevenlabs_api_key:
            raise AIProviderError("ELEVENLABS_API_KEY is not configured")
        if not settings.elevenlabs_voice_id:
            raise AIProviderError("ELEVENLABS_VOICE_ID is not configured")
        self._cache: dict[str, bytes] = {}

    def synthesize(self, text: str, *, language: str = "te-IN") -> Speech:
        cached = self._cache.get(text)
        if cached is not None:
            return Speech(
                audio=cached,
                mime_type=_mime_for(settings.elevenlabs_output_format),
                voice_id=settings.elevenlabs_voice_id,
                cached=True,
            )

        payload = {
            "text": text,
            "model_id": settings.elevenlabs_model_id,
        }
        try:
            response = httpx.post(
                f"{_BASE_URL}/text-to-speech/{settings.elevenlabs_voice_id}",
                json=payload,
                params={"output_format": settings.elevenlabs_output_format},
                headers={
                    "xi-api-key": settings.elevenlabs_api_key,
                    "accept": "audio/*",
                },
                timeout=settings.tts_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIProviderError(f"ElevenLabs synthesis failed: {exc}") from exc

        audio = response.content
        if len(self._cache) < settings.tts_cache_entries:
            self._cache[text] = audio
        return Speech(
            audio=audio,
            mime_type=_mime_for(settings.elevenlabs_output_format),
            voice_id=settings.elevenlabs_voice_id,
        )


class ElevenLabsSpeechProvider:
    """Speech-to-text via Scribe."""

    name = "elevenlabs"

    def __init__(self) -> None:
        if not settings.elevenlabs_api_key:
            raise AIProviderError("ELEVENLABS_API_KEY is not configured")

    def transcribe(self, audio: bytes, *, language: str | None = None) -> Transcription:
        data = {"model_id": settings.elevenlabs_stt_model_id}
        if language:
            data["language_code"] = language.split("-")[0]
        try:
            response = httpx.post(
                f"{_BASE_URL}/speech-to-text",
                headers={"xi-api-key": settings.elevenlabs_api_key},
                files={"file": ("audio", audio, "audio/mpeg")},
                data=data,
                timeout=settings.stt_timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise AIProviderError(f"ElevenLabs transcription failed: {exc}") from exc
        except ValueError as exc:
            raise AIProviderError("ElevenLabs returned a non-JSON transcription") from exc

        detected = body.get("language_code") or "te"
        return Transcription(
            text=body.get("text", ""),
            language=detected if "-" in detected else f"{detected}-IN",
            confidence=body.get("language_probability"),
            raw=body,
        )

    def detect_language(self, audio: bytes) -> str:
        return self.transcribe(audio).language


def _mime_for(output_format: str) -> str:
    if output_format.startswith("mp3"):
        return "audio/mpeg"
    if output_format.startswith("ulaw"):
        return "audio/basic"
    if output_format.startswith("pcm"):
        return "audio/L16"
    return "application/octet-stream"
