from functools import lru_cache

from backend.ai.base import AIProviderError, LLMProvider, SpeechProvider, VoiceProvider
from backend.ai.mock import (
    MockLLMProvider,
    MockSpeechProvider,
    MockStreamingLLMProvider,
    MockStreamingSpeechProvider,
    MockStreamingVoiceProvider,
    MockVoiceProvider,
)
from backend.ai.streaming import (
    StreamingLLMProvider,
    StreamingSpeechProvider,
    StreamingVoiceProvider,
)
from backend.core.config import settings


@lru_cache(maxsize=1)
def get_speech_provider() -> SpeechProvider:
    name = settings.speech_provider.lower()
    if name == "mock":
        return MockSpeechProvider()
    if name == "elevenlabs":
        from backend.ai.elevenlabs import ElevenLabsSpeechProvider

        return ElevenLabsSpeechProvider()
    raise AIProviderError(f"Unknown speech provider: {settings.speech_provider}")


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    name = settings.llm_provider.lower()
    if name == "mock":
        return MockLLMProvider()
    if name == "claude":
        from backend.ai.claude_llm import ClaudeLLMProvider

        return ClaudeLLMProvider()
    raise AIProviderError(f"Unknown LLM provider: {settings.llm_provider}")


@lru_cache(maxsize=1)
def get_voice_provider() -> VoiceProvider:
    name = settings.voice_provider.lower()
    if name == "mock":
        return MockVoiceProvider()
    if name == "elevenlabs":
        from backend.ai.elevenlabs import ElevenLabsVoiceProvider

        return ElevenLabsVoiceProvider()
    raise AIProviderError(f"Unknown voice provider: {settings.voice_provider}")


# ---------------------------------------------------------------------------
# Streaming providers (MVP section 9).
#
# A vendor may support streaming, batch, or both. These resolve separately
# from the batch getters so switching the turn-based API to a real provider
# does not force the media bridge to switch with it, and so a vendor with
# good batch Telugu but no streaming can still be used for one of the two.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_streaming_speech_provider() -> StreamingSpeechProvider:
    name = (settings.streaming_speech_provider or settings.speech_provider).lower()
    if name == "mock":
        return MockStreamingSpeechProvider()
    raise AIProviderError(
        f"{name!r} has no streaming speech implementation. Set "
        "STREAMING_SPEECH_PROVIDER=mock to run the media bridge without one."
    )


@lru_cache(maxsize=1)
def get_streaming_voice_provider() -> StreamingVoiceProvider:
    name = (settings.streaming_voice_provider or settings.voice_provider).lower()
    if name == "mock":
        return MockStreamingVoiceProvider()
    raise AIProviderError(
        f"{name!r} has no streaming voice implementation. Set "
        "STREAMING_VOICE_PROVIDER=mock to run the media bridge without one."
    )


@lru_cache(maxsize=1)
def get_streaming_llm_provider() -> StreamingLLMProvider:
    name = (settings.streaming_llm_provider or settings.llm_provider).lower()
    if name == "mock":
        return MockStreamingLLMProvider()
    raise AIProviderError(
        f"{name!r} has no streaming LLM implementation. Set "
        "STREAMING_LLM_PROVIDER=mock to run the media bridge without one."
    )


def reset_ai_provider_cache() -> None:
    """Drop cached providers (used by tests and after config changes)."""
    get_speech_provider.cache_clear()
    get_llm_provider.cache_clear()
    get_voice_provider.cache_clear()
    get_streaming_speech_provider.cache_clear()
    get_streaming_voice_provider.cache_clear()
    get_streaming_llm_provider.cache_clear()
