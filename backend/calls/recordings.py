"""Storing and reading back call recordings (MVP sections 29, 32).

Recordings are customer voice data. They are never given a public URL: the
bytes leave this system only through an authenticated, role-checked endpoint
that writes an audit entry naming who listened.
"""

import logging

from sqlalchemy.orm import Session

from backend.calls.models import CallAttempt, CallRecording
from backend.core.config import settings
from backend.storage.base import StorageError
from backend.storage.factory import get_storage_provider

logger = logging.getLogger(__name__)

#: Audio the telephony providers actually return.
ALLOWED_CONTENT_TYPES = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/ogg": "ogg",
    "audio/webm": "webm",
    "audio/basic": "au",       # 8-bit u-law, which is what a PSTN leg is
    "audio/x-mulaw": "ulaw",
}


class RecordingError(Exception):
    """Raised when a recording cannot be stored or read."""


def storage_key(attempt: CallAttempt, content_type: str) -> str:
    """Dated path, so a retention policy can sweep by age without a query."""
    extension = ALLOWED_CONTENT_TYPES.get(content_type, "bin")
    day = attempt.started_at.strftime("%Y/%m/%d") if attempt.started_at else "undated"
    return f"calls/{day}/call-{attempt.id}.{extension}"


def store_recording(
    db: Session,
    attempt: CallAttempt,
    data: bytes,
    *,
    content_type: str = "audio/mpeg",
    source_url: str | None = None,
    duration_seconds: int | None = None,
) -> CallRecording:
    """Put the audio in object storage and record where it went."""
    content_type = (content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise RecordingError(
            f"Unsupported recording type {content_type!r}; "
            f"expected one of {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
        )
    if not data:
        raise RecordingError("Recording is empty")
    if len(data) > settings.max_recording_bytes:
        raise RecordingError(
            f"Recording is {len(data)} bytes, over the "
            f"{settings.max_recording_bytes} byte limit"
        )

    key = storage_key(attempt, content_type)
    try:
        get_storage_provider().put(key, data, content_type=content_type)
    except StorageError as exc:
        raise RecordingError(str(exc)) from exc

    recording = attempt.recording
    if recording is None:
        recording = CallRecording(call_attempt_id=attempt.id)
        db.add(recording)

    recording.storage_key = key
    recording.content_type = content_type
    recording.size_bytes = len(data)
    recording.duration_seconds = duration_seconds or attempt.duration_seconds
    recording.source_url = source_url
    db.flush()
    return recording


def load_recording(db: Session, attempt: CallAttempt) -> tuple[bytes, str]:
    """The audio bytes and their content type."""
    recording = attempt.recording
    if recording is None:
        raise RecordingError("This call has no stored recording")
    try:
        return get_storage_provider().get(recording.storage_key), recording.content_type
    except StorageError as exc:
        raise RecordingError(str(exc)) from exc


def delete_recording(db: Session, attempt: CallAttempt) -> bool:
    """Remove the audio and its row. Used by the retention policy."""
    recording = attempt.recording
    if recording is None:
        return False
    try:
        get_storage_provider().delete(recording.storage_key)
    except StorageError as exc:
        # The row must still go: leaving it would claim audio that is gone.
        logger.warning("Could not delete recording %s: %s", recording.storage_key, exc)
    db.delete(recording)
    db.flush()
    return True


def fetch_from_provider(db: Session, attempt: CallAttempt, url: str) -> CallRecording | None:
    """Download a recording the telephony provider reported on its webhook.

    Off unless FETCH_PROVIDER_RECORDINGS is set: the mock places no real calls,
    and fetching a URL that arrived on a webhook is a network request that
    should be switched on deliberately. Failure is logged, never raised — a
    missing recording must not fail the call's status update.
    """
    if not settings.fetch_provider_recordings or not url:
        return None

    import httpx

    try:
        response = httpx.get(url, timeout=settings.recording_fetch_timeout_seconds,
                             follow_redirects=True)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "audio/mpeg")
        return store_recording(
            db, attempt, response.content, content_type=content_type, source_url=url
        )
    except Exception as exc:
        logger.warning("Could not fetch recording for call %s from %s: %s", attempt.id, url, exc)
        return None


def purge_expired_recordings(db: Session, *, older_than_days: int) -> int:
    """Delete recordings past the retention period (MVP section 32).

    Only the audio goes. The call, its transcript and its disposition stay, so
    reporting and compliance history are unaffected by how long voice data is
    kept — which is the point of having a retention period at all.
    """
    if older_than_days <= 0:
        return 0

    import datetime as dt

    from sqlalchemy import select

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=older_than_days)
    stale = db.scalars(
        select(CallRecording).where(CallRecording.created_at < cutoff)
    ).all()

    provider = get_storage_provider()
    for recording in stale:
        try:
            provider.delete(recording.storage_key)
        except StorageError as exc:
            # The row still goes: keeping it would claim audio that is gone.
            logger.warning("Could not delete recording %s: %s", recording.storage_key, exc)
        db.delete(recording)

    db.flush()
    if stale:
        logger.info("Purged %s recording(s) older than %s days", len(stale), older_than_days)
    return len(stale)
