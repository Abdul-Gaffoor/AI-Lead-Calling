"""The real-time media bridge (MVP §9).

A whole call is driven here as a list of byte strings: no network, no
provider account, no audio hardware. That is the point of keeping
`MediaSession` free of transport — the same frames a telephony provider
would send can be sent by a test.

Mock audio frames are UTF-8 text, matching the batch mocks, so a test
"speaks" by sending the words it wants transcribed.
"""

import io
import json

import pytest

from backend.ai.factory import reset_ai_provider_cache
from backend.ai.media import MediaSession, Phase
from backend.ai.models import ConversationState
from backend.ai.streaming import FRAME_MS
from backend.ai.vad import Activity, Endpointer, frame_energy
from backend.calls.models import CallAttempt, Disposition
from backend.core.database import SessionLocal
from backend.telephony.factory import reset_provider_cache

HEADERS = "Customer_Name,Mobile_Number,Lead_Source,Consent_Status,City,Monthly_Bill\n"
ALL_DAY = {"window_start": "00:00:00", "window_end": "23:59:59"}

#: 20 ms of 8 kHz 16-bit mono silence.
SILENCE = b"\x00" * 320


def speech(text: str) -> list[bytes]:
    """Frames that "say" `text`.

    The endpointer needs several consecutive loud frames before it calls
    something speech, so a single word still arrives as a few frames —
    which is what real audio does anyway.
    """
    words = text.split(" ")
    return [word.encode("utf-8") for word in words]


def silence(ms: int) -> list[bytes]:
    return [SILENCE] * (ms // FRAME_MS)


@pytest.fixture(autouse=True)
def fresh_providers():
    reset_provider_cache()
    reset_ai_provider_cache()
    yield
    reset_provider_cache()
    reset_ai_provider_cache()


@pytest.fixture
def answered_call(client, admin_headers):
    csv = HEADERS + "Ramesh,9876543210,WEBSITE,YES,Miyapur,7500\n"
    upload = client.post(
        "/leads/uploads", headers=admin_headers,
        files={"file": ("leads.csv", io.BytesIO(csv.encode()), "text/csv")},
    ).json()
    campaign = client.post(
        "/campaigns", headers=admin_headers,
        json={"name": "Media test", "concurrency": 2, "max_attempts": 3, **ALL_DAY},
    ).json()
    client.post(
        f"/campaigns/{campaign['id']}/leads", headers=admin_headers,
        json={"upload_id": upload["id"]},
    )
    client.post(f"/campaigns/{campaign['id']}/start", headers=admin_headers)
    client.post(f"/campaigns/{campaign['id']}/dispatch", headers=admin_headers)
    return client.get("/calls", headers=admin_headers).json()[0]


def _session(call_id: int):
    db = SessionLocal()
    attempt = db.get(CallAttempt, call_id)
    session, greeting = MediaSession.begin(db, attempt)
    return db, session, list(greeting)


# --- the detector ---------------------------------------------------------


def test_silence_has_no_energy_and_speech_does():
    assert frame_energy(SILENCE) == 0.0
    assert frame_energy("మాట్లాడుతున్నాను".encode()) > 0.02


def test_an_odd_trailing_byte_does_not_derail_the_detector():
    """A truncated sample must not be misread as noise."""
    assert frame_energy(b"\x00" * 319 + b"\x00") == 0.0
    assert frame_energy(b"\x01") == 0.0


def test_a_single_loud_frame_is_not_speech():
    """One click, door or cough should not open an utterance."""
    endpointer = Endpointer()
    assert endpointer.push(b"hi") is Activity.SILENCE
    assert not endpointer.speaking


def test_speech_then_silence_produces_one_endpoint():
    endpointer = Endpointer(silence_ms=100)
    for _ in range(5):
        endpointer.push(b"talking")
    assert endpointer.speaking

    activities = [endpointer.push(SILENCE) for _ in range(5)]
    assert Activity.ENDPOINT in activities
    assert activities.count(Activity.ENDPOINT) == 1, "a turn must end exactly once"
    assert not endpointer.speaking


def test_a_short_gap_between_words_does_not_end_the_turn():
    """People pause mid-sentence. Cutting them off is worse than waiting."""
    endpointer = Endpointer(silence_ms=700)
    for _ in range(5):
        endpointer.push(b"first")
    # 200 ms of thinking
    gaps = [endpointer.push(SILENCE) for _ in range(10)]
    assert Activity.ENDPOINT not in gaps
    assert endpointer.speaking


def test_an_endless_utterance_is_eventually_cut():
    endpointer = Endpointer(max_utterance_ms=200)
    for _ in range(20):
        endpointer.push(b"going on and on")
    assert endpointer.exceeded_max_utterance()


# --- the bridge -----------------------------------------------------------


def test_the_call_opens_with_the_scripted_disclosure(answered_call):
    """CLAUDE.md rule 2 holds on a live call, not just the turn-based API."""
    db, session, greeting = _session(answered_call["id"])
    try:
        spoken = b"".join(chunk.audio for chunk in greeting).decode()
        assert "AI" in spoken
        # Nothing was heard yet, so nothing could have been generated.
        assert session.turns == []
    finally:
        session.close()
        db.close()


def test_a_full_turn_transcribes_decides_and_replies(answered_call):
    db, session, _ = _session(answered_call["id"])
    try:
        out = []
        for frame in speech("మా ఇల్లు own house") + silence(800):
            out += session.push(frame)

        assert out, "the AI said nothing after the customer finished"
        assert len(session.turns) == 1
        assert "own house" in session.turns[0].transcript
    finally:
        session.close()
        db.close()


def test_the_first_syllable_is_not_lost(answered_call):
    """
    The endpointer needs several frames before it calls something speech.
    Those frames still have to reach the transcriber, or every answer
    arrives with its opening word missing — which is the difference
    between "వద్దు" and nothing at all.
    """
    db, session, _ = _session(answered_call["id"])
    try:
        for frame in speech("ఒకటి రెండు మూడు నాలుగు") + silence(800):
            session.push(frame)

        transcript = session.turns[0].transcript
        assert transcript.startswith("ఒకటి"), (
            f"the opening word was dropped: {transcript!r}"
        )
    finally:
        session.close()
        db.close()


def test_silence_alone_produces_no_turn(answered_call):
    db, session, _ = _session(answered_call["id"])
    try:
        for frame in silence(2000):
            session.push(frame)
        assert session.turns == []
    finally:
        session.close()
        db.close()


def test_barge_in_stops_the_ai_talking(answered_call):
    """
    While the AI is speaking, the customer starting to talk must cut the
    playback. Talking over a customer is the most obviously robotic thing
    a voice agent does.
    """
    db, session, _ = _session(answered_call["id"])
    try:
        assert session.phase is Phase.SPEAKING  # still playing the greeting
        for frame in speech("ఆగండి ఆగండి ఒక్క నిమిషం"):
            session.push(frame)
        assert session.phase is Phase.LISTENING
    finally:
        session.close()
        db.close()


def test_opt_out_on_a_live_call_suppresses_the_number(answered_call):
    """
    CLAUDE.md rule 3 over the media path. The orchestrator sees the final
    transcript, so the deterministic match still runs — this is the reason
    the bridge does STT -> LLM -> TTS rather than speech-to-speech.
    """
    db, session, _ = _session(answered_call["id"])
    try:
        for frame in speech("నాకు మళ్లీ call చేయకండి") + silence(800):
            session.push(frame)

        db.flush()
        assert session.conversation.state is ConversationState.ENDED
        assert session.conversation.final_intent == "OPT_OUT"
        assert session.should_hang_up

        attempt = db.get(CallAttempt, answered_call["id"])
        assert attempt.disposition is Disposition.DO_NOT_CALL
    finally:
        session.close()
        db.close()


def test_a_multi_turn_call_runs_to_a_disposition(answered_call):
    db, session, _ = _session(answered_call["id"])
    try:
        for utterance in ("own house roof 7500", "site survey కావాలి"):
            for frame in speech(utterance) + silence(800):
                session.push(frame)
            if session.should_hang_up:
                break

        assert session.turns
        assert session.conversation.state is ConversationState.ENDED
        summary = session.latency_summary()
        assert summary["turns"] == len(session.turns)
        assert summary["first_audio_ms_p50"] is not None
    finally:
        session.close()
        db.close()


def test_an_idle_line_is_closed_rather_than_held(answered_call):
    db, session, _ = _session(answered_call["id"])
    try:
        from backend.core.config import settings

        for frame in silence((settings.media_idle_timeout_s + 1) * 1000):
            session.push(frame)
        assert session.phase is Phase.ENDED
        assert session.should_hang_up
    finally:
        session.close()
        db.close()


# --- the socket -----------------------------------------------------------


def test_the_media_socket_rejects_a_bad_token(client, answered_call, monkeypatch):
    from backend.core import config

    monkeypatch.setattr(config.settings, "telephony_webhook_token", "right-token")
    with pytest.raises(Exception):
        with client.websocket_connect(
            f"/media/calls/{answered_call['id']}?token=wrong-token"
        ):
            pass


def test_the_media_socket_refuses_when_no_token_is_configured(client, answered_call, monkeypatch):
    """
    An open socket into the conversation engine is not a sensible default
    for a deployment that has not turned on telephony.
    """
    from backend.core import config

    monkeypatch.setattr(config.settings, "telephony_webhook_token", "")
    with pytest.raises(Exception):
        with client.websocket_connect(f"/media/calls/{answered_call['id']}"):
            pass


def test_the_media_socket_carries_a_whole_call(client, answered_call, monkeypatch):
    from backend.core import config

    monkeypatch.setattr(config.settings, "telephony_webhook_token", "media-token")

    with client.websocket_connect(
        f"/media/calls/{answered_call['id']}?token=media-token"
    ) as socket:
        # The greeting arrives as several chunks so playback can start on
        # the first clause; `reply_end` says when it is complete.
        greeting = b""
        while True:
            message = socket.receive()
            if message.get("text"):
                assert json.loads(message["text"])["event"] == "reply_end"
                break
            greeting += message["bytes"]
        assert "AI" in greeting.decode()

        for frame in speech("నాకు మళ్లీ call చేయకండి") + silence(800):
            socket.send_bytes(frame)

        # The reply, then the hangup signal.
        saw_hangup = False
        for _ in range(10):
            message = socket.receive()
            if message.get("text"):
                saw_hangup = json.loads(message["text"]).get("event") == "hangup"
                if saw_hangup:
                    break
        assert saw_hangup, "the socket never signalled hangup after an opt-out"

    with SessionLocal() as db:
        attempt = db.get(CallAttempt, answered_call["id"])
        assert attempt.disposition is Disposition.DO_NOT_CALL


def test_an_unknown_call_is_refused(client, monkeypatch):
    from backend.core import config

    monkeypatch.setattr(config.settings, "telephony_webhook_token", "media-token")
    with client.websocket_connect("/media/calls/999999?token=media-token") as socket:
        message = socket.receive()
        assert "unknown call" in str(message)
