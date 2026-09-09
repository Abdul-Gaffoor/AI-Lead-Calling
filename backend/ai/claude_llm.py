"""Claude LLM provider.

Uses structured outputs so every turn returns a validated TurnDecision
rather than free text that would need parsing.
"""

import logging

import anthropic
from pydantic import BaseModel

from backend.ai.base import AIProviderError, TurnDecision
from backend.core.config import settings

logger = logging.getLogger(__name__)


class ClaudeLLMProvider:
    name = "claude"

    def __init__(self) -> None:
        if not settings.anthropic_api_key:
            raise AIProviderError("ANTHROPIC_API_KEY is not configured")
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            # A live call cannot wait: fail fast and let the orchestrator
            # fall back to a safe reply rather than leaving dead air.
            timeout=settings.llm_timeout_seconds,
            max_retries=1,
        )

    def generate(self, *, system: str, messages: list[dict]) -> TurnDecision:
        try:
            response = self._client.messages.parse(
                model=settings.llm_model,
                max_tokens=settings.llm_max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                # Conversational turns are latency-sensitive; low effort keeps
                # the customer from hearing dead air. Thinking stays adaptive.
                output_config={"effort": settings.llm_effort},
                output_format=TurnDecision,
            )
        except anthropic.APIError as exc:
            raise AIProviderError(f"Claude request failed: {exc}") from exc

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            raise AIProviderError(f"Claude declined to answer (category={category})")

        decision = response.parsed_output
        if decision is None:
            raise AIProviderError("Claude returned no parsed decision")
        return decision

    def extract(self, *, system: str, transcript: str, schema: type[BaseModel]) -> BaseModel:
        try:
            response = self._client.messages.parse(
                model=settings.llm_model,
                max_tokens=settings.llm_max_tokens,
                system=system,
                messages=[{"role": "user", "content": transcript}],
                output_config={"effort": settings.llm_effort},
                output_format=schema,
            )
        except anthropic.APIError as exc:
            raise AIProviderError(f"Claude extraction failed: {exc}") from exc

        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise AIProviderError("Claude did not return an extraction")
        return response.parsed_output
