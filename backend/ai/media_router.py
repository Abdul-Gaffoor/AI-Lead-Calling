"""WebSocket transport for the media bridge (MVP section 9).

The telephony provider opens one socket per call and streams the customer's
audio down it; we stream the AI's speech back. All the thinking is in
`media.MediaSession` — this module only moves bytes and handles the things
sockets do that a session should not have to know about: authentication,
disconnects, and a protocol frame that is text rather than audio.

Authenticated with the same shared token as the status webhook. A user JWT
is not available here: the party connecting is a telephony provider, not a
person, and this endpoint is necessarily reachable from the internet.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from backend.ai.media import MediaSession
from backend.calls.models import CallAttempt
from backend.core.config import settings
from backend.core.database import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/media", tags=["media"])

#: Closed before the handshake completes, so an unauthenticated caller
#: never reaches the orchestrator.
POLICY_VIOLATION = 1008
NORMAL_CLOSURE = 1000


def _authorised(token: str | None, header_token: str | None) -> bool:
    expected = settings.telephony_webhook_token
    if not expected:
        # No token configured means this deployment has not turned on real
        # telephony. Refuse rather than accept anything: an open socket
        # into the conversation engine is not a sensible default.
        return False
    return (token or header_token) == expected


async def _send_audio(websocket: WebSocket, chunks) -> bool:
    """Play a reply, then mark its end.

    A reply arrives as several chunks so playback can start on the first
    clause. Without a marker the far end cannot tell "more audio coming"
    from "your turn to speak", and would either talk over the rest of the
    sentence or sit waiting through it.
    """
    sent = False
    for chunk in chunks:
        await websocket.send_bytes(chunk.audio)
        sent = True
    if sent:
        await websocket.send_text(json.dumps({"event": "reply_end"}))
    return sent


@router.websocket("/calls/{call_id}")
async def call_media(
    websocket: WebSocket,
    call_id: int,
    token: str | None = Query(default=None),
) -> None:
    """Carry one call's audio.

    Protocol, deliberately minimal so any provider can meet it:

    * binary message  -> 20 ms of customer audio, 8 kHz 16-bit mono PCM
    * text message    -> a JSON control frame; `{"event": "stop"}` ends the call
    * binary reply    -> audio to play to the customer
    * text reply      -> `{"event": "reply_end"}` when a reply is complete,
                         `{"event": "hangup"}` when the call is over
    """
    header_token = websocket.headers.get("x-webhook-token")
    if not _authorised(token, header_token):
        await websocket.close(code=POLICY_VIOLATION, reason="Invalid media token")
        return

    await websocket.accept()

    db: Session = SessionLocal()
    session: MediaSession | None = None
    try:
        attempt = db.get(CallAttempt, call_id)
        if attempt is None:
            await websocket.send_text(json.dumps({"event": "error", "detail": "unknown call"}))
            await websocket.close(code=POLICY_VIOLATION)
            return

        session, greeting = MediaSession.begin(db, attempt)
        db.commit()
        await _send_audio(websocket, greeting)

        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                break

            if (text := message.get("text")) is not None:
                if _is_stop(text):
                    break
                continue

            frame = message.get("bytes")
            if not frame:
                continue

            chunks = session.push(frame)
            if chunks:
                await _send_audio(websocket, chunks)
                # Each completed turn is a disposition, a score and possibly
                # an opportunity. Commit per turn so a dropped call keeps
                # what the customer already told us.
                db.commit()

            if session.should_hang_up:
                await websocket.send_text(json.dumps({"event": "hangup"}))
                break

    except WebSocketDisconnect:
        logger.info("Media socket closed by the provider for call %s", call_id)
    except Exception:
        logger.exception("Media bridge failed for call %s", call_id)
        db.rollback()
    finally:
        if session is not None:
            session.close()
        try:
            db.commit()
        except Exception:
            db.rollback()
        db.close()
        try:
            await websocket.close(code=NORMAL_CLOSURE)
        except RuntimeError:
            pass  # already closed by the other end


def _is_stop(text: str) -> bool:
    try:
        return json.loads(text).get("event") in ("stop", "stopped", "hangup")
    except (ValueError, AttributeError):
        return False
