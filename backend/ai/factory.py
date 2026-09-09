from functools import lru_cache

from backend.ai.base import AIProviderError, LLMProvider, SpeechProvider, VoiceProvider
from backend.ai.mock import MockLLMProvider, MockSpeechProvider, MockVoiceProvider
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


def reset_ai_provider_cache() -> None:
    """Drop cached providers (used by tests and after config changes)."""
    get_speech_provider.cache_clear()
    get_llm_provider.cache_clear()
    get_voice_provider.cache_clear()
