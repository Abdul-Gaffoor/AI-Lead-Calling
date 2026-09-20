import datetime as dt

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from backend.auth.dependencies import require_roles
from backend.auth.models import Role, User
from backend.calls.models import CallAttempt, Disposition
from backend.calls.recordings import RecordingError, load_recording, store_recording
from backend.compliance.service import log_action
from backend.core.database import get_db
from backend.quality import service as quality
from backend.quality.schemas import (
    CallReviewDetail,
    QualitySummary,
    QueueItem,
    RecordingOut,
    ReviewIn,
    ReviewOut,
)

router = APIRouter(prefix="/quality", tags=["quality"])

#: Recordings and transcripts are customer conversations. MVP section 32 lists
#: recording permissions as a security control, so playback and review are
#: limited to the roles that manage the platform rather than everyone signed in.
_reviewer = require_roles(Role.SUPER_ADMIN, Role.SALES_MANAGER)


def _attempt(db: Session, call_id: int) -> CallAttempt:
    attempt = db.get(CallAttempt, call_id)
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such call")
    return attempt


@router.get("/calls", response_model=list[QueueItem])
def review_queue(
    reviewed: bool | None = None,
    disposition: Disposition | None = None,
    since: dt.date | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(_reviewer),
):
    """Answered calls, newest first. `reviewed=false` is the work queue."""
    return quality.reviewable_calls(
        db, reviewed=reviewed, disposition=disposition, since=since, limit=min(limit, 200)
    )


@router.get("/calls/{call_id}", response_model=CallReviewDetail)
def call_detail(
    call_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(_reviewer),
):
    """The whole call in one place: transcript, extraction, summary, score."""
    return quality.call_for_review(db, _attempt(db, call_id))


@router.post("/calls/{call_id}/review", response_model=ReviewOut,
             status_code=status.HTTP_201_CREATED)
def submit_review(
    call_id: int,
    payload: ReviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(_reviewer),
):
    attempt = _attempt(db, call_id)
    try:
        review = quality.submit_review(
            db, attempt, user,
            verdict=payload.verdict, flags=payload.flags, note=payload.note,
        )
    except quality.QualityError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    log_action(
        db, actor=user, action="CALL_REVIEWED", entity=f"call:{call_id}",
        details={"verdict": payload.verdict.value,
                 "flags": [flag.value for flag in payload.flags]},
    )
    db.commit()
    db.refresh(review)
    return review


@router.get("/summary", response_model=QualitySummary)
def summary(
    since: dt.date | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(_reviewer),
):
    """What reviewers are finding, for the AI-quality process (MVP section 38)."""
    return quality.quality_summary(db, since=since)


# --- recordings --------------------------------------------------------------


@router.put("/calls/{call_id}/recording", response_model=RecordingOut)
async def upload_recording(
    call_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(_reviewer),
):
    """Attach a recording to a call.

    Used when the telephony provider pushes audio rather than a URL, and to
    load recordings for review during the pilot.
    """
    attempt = _attempt(db, call_id)
    try:
        recording = store_recording(
            db, attempt, await file.read(),
            content_type=file.content_type or "audio/mpeg",
        )
    except RecordingError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    log_action(db, actor=user, action="RECORDING_STORED", entity=f"call:{call_id}",
               details={"bytes": recording.size_bytes})
    db.commit()
    db.refresh(recording)
    return recording


@router.get("/calls/{call_id}/recording")
def play_recording(
    call_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_reviewer),
):
    """Stream the audio back. Who listened, and when, is auditable."""
    attempt = _attempt(db, call_id)
    try:
        data, content_type = load_recording(db, attempt)
    except RecordingError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

    log_action(db, actor=user, action="RECORDING_PLAYED", entity=f"call:{call_id}")
    db.commit()
    return Response(
        content=data,
        media_type=content_type,
        headers={
            # Never cached by a shared proxy: this is customer voice data.
            "Cache-Control": "no-store, private",
            "Content-Disposition": f'inline; filename="call-{call_id}"',
        },
    )
