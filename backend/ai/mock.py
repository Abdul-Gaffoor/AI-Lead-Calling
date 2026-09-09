"""Mock AI providers.

Used by the test suite and for running the platform before AI vendor
accounts exist. They are deterministic and never make a network call, so a
whole campaign can be exercised end to end with no spend.
"""

from pydantic import BaseModel

from backend.ai.base import Intent, Speech, Transcription, TurnDecision
from backend.leads.models import ServiceType

#: Phrases that drive the mock's behaviour, so tests can steer a conversation.
_INTENT_CUES: list[tuple[tuple[str, ...], Intent]] = [
    (("don't call", "dont call", "do not call", "చేయకండి"), Intent.OPT_OUT),
    (("sales person", "human", "manishi", "మనిషి"), Intent.HUMAN_REQUEST),
    (("not interested", "no thanks", "వద్దు"), Intent.NOT_INTERESTED),
    (("call me tomorrow", "callback", "రేపు"), Intent.CALLBACK_REQUESTED),
    (("site survey", "survey", "సర్వే"), Intent.SITE_SURVEY_REQUESTED),
    (("already have solar", "existing customer"), Intent.EXISTING_CUSTOMER),
    (("not working", "cleaning", "maintenance", "service"), Intent.SERVICE_REQUEST),
    (("wrong number",), Intent.WRONG_NUMBER),
]


class MockSpeechProvider:
    name = "mock"

    def __init__(self) -> None:
        self.transcribed: list[bytes] = []

    def transcribe(self, audio: bytes, *, language: str | None = None) -> Transcription:
        self.transcribed.append(audio)
        # Mock audio is just UTF-8 text, so tests can drive real conversations.
        text = audio.decode("utf-8", errors="replace")
        return Transcription(text=text, language=language or self.detect_language(audio))

    def detect_language(self, audio: bytes) -> str:
        text = audio.decode("utf-8", errors="replace")
        has_telugu = any("ఀ" <= ch <= "౿" for ch in text)
        return "te-IN" if has_telugu else "en-IN"


class MockLLMProvider:
    name = "mock"

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def generate(self, *, system: str, messages: list[dict]) -> TurnDecision:
        self.calls.append(messages)
        last = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                last = str(message.get("content", "")).lower()
                break

        for cues, intent in _INTENT_CUES:
            if any(cue in last for cue in cues):
                return TurnDecision(
                    reply=_REPLIES[intent],
                    intent=intent,
                    language="te-IN" if any("ఀ" <= c <= "౿" for c in last) else "en-IN",
                    service=ServiceType.RESIDENTIAL_SOLAR if intent is Intent.SITE_SURVEY_REQUESTED else None,
                    extracted={"site_survey": True} if intent is Intent.SITE_SURVEY_REQUESTED else {},
                    callback_at="2026-09-10T11:00:00+05:30"
                    if intent is Intent.CALLBACK_REQUESTED
                    else None,
                )

        extracted: dict = {}
        if "own" in last and "house" in last:
            extracted["property_owned"] = True
        if "roof" in last:
            extracted["roof_available"] = True
        for token in last.replace(",", " ").split():
            if token.isdigit() and len(token) >= 3:
                extracted.setdefault("monthly_bill", int(token))

        return TurnDecision(
            reply="మీ విద్యుత్ బిల్లు నెలకు ఎంత వస్తుంది?",
            intent=Intent.CONTINUE,
            language="te-IN",
            service=ServiceType.RESIDENTIAL_SOLAR if extracted else None,
            extracted=extracted,
        )

    def extract(self, *, system: str, transcript: str, schema: type[BaseModel]) -> BaseModel:
        return schema()


_REPLIES = {
    Intent.OPT_OUT: "క్షమించండి. మిమ్మల్ని మళ్లీ contact చేయము. ధన్యవాదాలు.",
    Intent.HUMAN_REQUEST: "తప్పకుండా. మా sales executive ని connect చేస్తున్నాను.",
    Intent.NOT_INTERESTED: "సరే, మీ సమయానికి ధన్యవాదాలు.",
    Intent.CALLBACK_REQUESTED: "సరే, రేపు call చేస్తాము. ధన్యవాదాలు.",
    Intent.SITE_SURVEY_REQUESTED: "చాలా సంతోషం. మా engineer site survey కి వస్తారు.",
    Intent.EXISTING_CUSTOMER: "మీరు ఇప్పటికే మా customer. service team కి forward చేస్తున్నాను.",
    Intent.SERVICE_REQUEST: "మీ service request ని note చేసుకున్నాము.",
    Intent.WRONG_NUMBER: "క్షమించండి, తప్పు number కి call చేశాము.",
}


class MockVoiceProvider:
    name = "mock"

    def __init__(self) -> None:
        self.synthesized: list[str] = []

    def synthesize(self, text: str, *, language: str = "te-IN") -> Speech:
        self.synthesized.append(text)
        return Speech(audio=text.encode("utf-8"), mime_type="audio/mpeg", voice_id="mock-voice")
