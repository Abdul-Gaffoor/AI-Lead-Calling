"""Conversation orchestrator (MVP section 9).

    Customer audio -> STT -> orchestrator -> LLM (+ state + business rules)
                  -> structured decision -> TTS -> customer

The orchestrator owns everything the LLM must not be trusted with: the AI
disclosure, opt-out detection, turn limits, and translating an intent into
a call disposition.
"""

import datetime as dt
import logging
import time

from sqlalchemy.orm import Session

from backend.ai.base import (
    TERMINAL_INTENTS,
    AIProviderError,
    Intent,
    Speech,
    TurnDecision,
)
from backend.ai.factory import get_llm_provider, get_speech_provider, get_voice_provider
from backend.ai.models import Conversation, ConversationState, ConversationTurn, Speaker
from backend.ai.prompts import SAFE_FALLBACK_REPLY, build_system_prompt, opening_line
from backend.ai.qualification import is_sufficient, missing_fields
from backend.calls.models import CallAttempt, Disposition
from backend.calls.service import record_disposition, utcnow
from backend.core.config import settings
from backend.customers.models import Customer
from backend.leads.models import Lead

logger = logging.getLogger(__name__)

#: Deterministic opt-out backstop. The LLM is asked to detect this too, but a
#: customer's request not to be called must never depend on model judgement
#: (MVP section 30).
OPT_OUT_PHRASES = (
    "call cheyyakandi",
    "call cheyyaku",
    "don't call",
    "dont call",
    "do not call",
    "stop calling",
    "remove my number",
    "మళ్లీ call చేయకండి",
    "call చేయకండి",
    "చేయవద్దు",
    "ఫోన్ చేయకండి",
)

#: Likewise for an explicit request to speak to a person (MVP section 23).
HUMAN_REQUEST_PHRASES = (
    "sales person",
    "speak to a person",
    "talk to human",
    "manishi tho",
    "మనిషితో",
    "sales person tho",
)

INTENT_DISPOSITIONS: dict[Intent, Disposition] = {
    # Lead scoring in Sprint 6 replaces this with HOT/WARM/COLD by score.
    Intent.QUALIFIED: Disposition.QUALIFIED_WARM,
    Intent.SITE_SURVEY_REQUESTED: Disposition.SITE_SURVEY_REQUESTED,
    Intent.CALLBACK_REQUESTED: Disposition.CALLBACK_REQUESTED,
    Intent.HUMAN_REQUEST: Disposition.HUMAN_TRANSFER,
    Intent.NOT_INTERESTED: Disposition.NOT_INTERESTED,
    Intent.OPT_OUT: Disposition.DO_NOT_CALL,
    Intent.EXISTING_CUSTOMER: Disposition.EXISTING_CUSTOMER,
    Intent.SERVICE_REQUEST: Disposition.SERVICE_REQUEST,
    Intent.WRONG_NUMBER: Disposition.WRONG_NUMBER,
}


class ConversationError(Exception):
    """Raised when a conversation cannot be started or continued."""


def _matches(text: str, phrases: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(phrase.lower() in lowered for phrase in phrases)


def start_conversation(db: Session, attempt: CallAttempt) -> tuple[Conversation, Speech]:
    """Open a conversation for an answered call.

    The first thing said is always the AI disclosure (MVP section 8) — it is
    scripted, never model-generated, so it cannot be skipped or reworded.
    """
    existing = (
        db.query(Conversation).filter(Conversation.call_attempt_id == attempt.id).one_or_none()
    )
    if existing is not None:
        raise ConversationError("A conversation already exists for this call")

    customer = db.get(Customer, attempt.customer_id)
    if customer is None:
        raise ConversationError("Call has no customer record")
    lead = db.get(Lead, attempt.lead_id) if attempt.lead_id else None

    language = (lead.language if lead and lead.language else None) or settings.default_language
    language = "en-IN" if str(language).lower().startswith("en") else "te-IN"

    conversation = Conversation(
        call_attempt_id=attempt.id,
        lead_id=attempt.lead_id,
        customer_id=customer.id,
        language=language,
        service=lead.interested_service if lead else None,
        collected=_seed_from_lead(lead),
        state=ConversationState.GREETING,
    )
    db.add(conversation)
    db.flush()

    greeting = opening_line(customer.name, language)
    _add_turn(db, conversation, Speaker.AI, greeting, language)
    speech = _synthesize(greeting, language)
    return conversation, speech


def handle_turn(
    db: Session,
    conversation: Conversation,
    *,
    text: str | None = None,
    audio: bytes | None = None,
    actor=None,
) -> tuple[TurnDecision, Speech]:
    """Process one customer utterance and produce the AI's reply."""
    if conversation.state is ConversationState.ENDED:
        raise ConversationError("This conversation has already ended")
    if text is None and audio is None:
        raise ConversationError("Provide either text or audio for the customer turn")

    started = time.monotonic()

    if text is None:
        transcription = get_speech_provider().transcribe(audio, language=conversation.language)
        text = transcription.text
        if transcription.language:
            conversation.language = transcription.language

    _add_turn(db, conversation, Speaker.CUSTOMER, text, conversation.language)
    conversation.state = ConversationState.QUALIFYING

    decision = _decide(db, conversation, text)

    # Deterministic overrides — these must not depend on model judgement.
    if _matches(text, OPT_OUT_PHRASES):
        decision.intent = Intent.OPT_OUT
    elif _matches(text, HUMAN_REQUEST_PHRASES) and decision.intent is Intent.CONTINUE:
        decision.intent = Intent.HUMAN_REQUEST

    if decision.service is not None:
        conversation.service = decision.service
    if decision.extracted:
        # Never let a later turn blank out a value already given.
        merged = dict(conversation.collected or {})
        merged.update({k: v for k, v in decision.extracted.items() if v not in (None, "")})
        conversation.collected = merged
    if decision.language:
        conversation.language = decision.language

    conversation.turn_count += 1

    # Stop asking once the service's key fields are answered.
    if (
        decision.intent is Intent.CONTINUE
        and is_sufficient(conversation.service, conversation.collected or {})
    ):
        decision.intent = Intent.QUALIFIED

    # A phone call cannot go on forever.
    if decision.intent is Intent.CONTINUE and conversation.turn_count >= settings.max_conversation_turns:
        decision.intent = (
            Intent.QUALIFIED
            if conversation.collected
            else Intent.NOT_INTERESTED
        )

    latency_ms = int((time.monotonic() - started) * 1000)
    _add_turn(db, conversation, Speaker.AI, decision.reply, decision.language, latency_ms)

    if decision.intent in TERMINAL_INTENTS:
        _finalize(db, conversation, decision, actor=actor)

    return decision, _synthesize(decision.reply, decision.language)


def _decide(db: Session, conversation: Conversation, text: str) -> TurnDecision:
    """Ask the LLM what to say next, falling back safely if it cannot answer."""
    messages = [{"role": "user", "content": _context_block(db, conversation)}]
    for turn in conversation.turns:
        messages.append(
            {
                "role": "assistant" if turn.speaker is Speaker.AI else "user",
                "content": turn.text,
            }
        )

    try:
        return get_llm_provider().generate(system=build_system_prompt(), messages=messages)
    except AIProviderError as exc:
        # Dead air is worse than a handover: apologise and route to a human.
        logger.warning("LLM turn failed for conversation %s: %s", conversation.id, exc)
        return TurnDecision(
            reply=SAFE_FALLBACK_REPLY,
            language=conversation.language,
            intent=Intent.HUMAN_REQUEST,
        )


def _context_block(db: Session, conversation: Conversation) -> str:
    """What the AI already knows, so it never re-asks (MVP section 12)."""
    customer = db.get(Customer, conversation.customer_id)
    lead = db.get(Lead, conversation.lead_id) if conversation.lead_id else None

    lines = ["## This customer", f"Name: {customer.name if customer else 'unknown'}"]
    if lead is not None:
        for label, value in (
            ("City", lead.city),
            ("District", lead.district),
            ("Pincode", lead.pincode),
            ("Enquired about", lead.interested_service.value if lead.interested_service else None),
            ("Monthly bill (Rs)", lead.monthly_bill),
            ("Monthly units", lead.monthly_units),
            ("Lead source", lead.source),
        ):
            if value not in (None, ""):
                lines.append(f"{label}: {value}")
    if customer is not None and customer.is_existing_customer:
        lines.append("NOTE: already a Swaraj customer — do not pitch a new system.")

    collected = conversation.collected or {}
    if collected:
        lines.append("\n## Already gathered on this call")
        lines.extend(f"{key}: {value}" for key, value in collected.items())

    remaining = missing_fields(conversation.service, collected)
    if remaining:
        lines.append("\n## Still needed")
        lines.append(", ".join(remaining))

    lines.append("\nContinue the call. Reply with your next spoken line.")
    return "\n".join(lines)


def _finalize(db: Session, conversation: Conversation, decision: TurnDecision, *, actor=None) -> None:
    """Close the conversation and record the outcome on the call."""
    conversation.state = ConversationState.ENDED
    conversation.ended_at = utcnow()
    conversation.final_intent = decision.intent.value
    conversation.summary = build_summary(db, conversation)

    attempt = db.get(CallAttempt, conversation.call_attempt_id)
    if attempt is None:
        return

    disposition = INTENT_DISPOSITIONS.get(decision.intent)
    if disposition is None:
        return

    callback_at = None
    if decision.callback_at:
        try:
            callback_at = dt.datetime.fromisoformat(decision.callback_at)
            if callback_at.tzinfo is None:
                callback_at = callback_at.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            logger.warning("Unparseable callback_at %r", decision.callback_at)

    record_disposition(
        db,
        attempt,
        disposition,
        summary=conversation.summary,
        ai_payload=structured_output(conversation),
        callback_at=callback_at,
        actor=actor,
    )


def structured_output(conversation: Conversation) -> dict:
    """The structured record of the call (MVP section 21).

    Lead score and HOT/WARM/COLD classification are added in Sprint 6 by the
    scoring engine; they are deliberately not guessed here.
    """
    payload = {
        "lead_id": conversation.lead_id,
        "language": conversation.language,
        "service": conversation.service.value if conversation.service else None,
        "intent": conversation.final_intent,
        "turns": conversation.turn_count,
    }
    payload.update(conversation.collected or {})
    return payload


def build_summary(db: Session, conversation: Conversation) -> str:
    """Human-readable summary for the sales executive (MVP section 21)."""
    customer = db.get(Customer, conversation.customer_id)
    lead = db.get(Lead, conversation.lead_id) if conversation.lead_id else None

    header = customer.name if customer else "Customer"
    if lead is not None and lead.city:
        header = f"{header} – {lead.city}"

    lines = [header, ""]
    if conversation.service is not None:
        lines.append(f"Service: {conversation.service.value.replace('_', ' ').title()}")
    for key, value in (conversation.collected or {}).items():
        lines.append(f"{key.replace('_', ' ').capitalize()}: {value}")
    if conversation.final_intent:
        lines.append("")
        lines.append(f"Outcome: {conversation.final_intent.replace('_', ' ').title()}")
    return "\n".join(lines)


def _seed_from_lead(lead: Lead | None) -> dict:
    """Pre-fill what the lead record already tells us."""
    if lead is None:
        return {}
    seed: dict = {}
    if lead.monthly_bill is not None:
        seed["monthly_bill"] = float(lead.monthly_bill)
    if lead.monthly_units is not None:
        seed["monthly_units"] = float(lead.monthly_units)
    if lead.city:
        seed["location"] = lead.city
    return seed


def _add_turn(
    db: Session,
    conversation: Conversation,
    speaker: Speaker,
    text: str,
    language: str | None,
    latency_ms: int | None = None,
) -> ConversationTurn:
    turn = ConversationTurn(
        conversation_id=conversation.id,
        index=len(conversation.turns),
        speaker=speaker,
        text=text,
        language=language,
        latency_ms=latency_ms,
    )
    db.add(turn)
    conversation.turns.append(turn)
    return turn


def _synthesize(text: str, language: str) -> Speech:
    try:
        return get_voice_provider().synthesize(text, language=language)
    except AIProviderError as exc:
        # The words still reach the caller through the transcript/telephony
        # layer; losing audio must not lose the conversation.
        logger.warning("Speech synthesis failed: %s", exc)
        return Speech(audio=b"", mime_type="application/octet-stream")
