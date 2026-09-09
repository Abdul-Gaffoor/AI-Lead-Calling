import base64

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.ai.conversation import (
    ConversationError,
    handle_turn,
    start_conversation,
    structured_output,
)
from backend.ai.models import Conversation
from backend.ai.qualification import missing_fields
from backend.ai.schemas import (
    ConversationDetail,
    ConversationOut,
    ConversationStart,
    ReplyOut,
    TurnRequest,
)
from backend.auth.dependencies import get_current_user
from backend.auth.models import User
from backend.calls.models import CallAttempt
from backend.core.database import get_db

router = APIRouter(prefix="/ai", tags=["ai"])


def _get_conversation(db: Session, conversation_id: int) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


@router.post("/conversations", response_model=ReplyOut, status_code=status.HTTP_201_CREATED)
def begin_conversation(
    payload: ConversationStart,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Start the AI conversation for an answered call.

    Returns the AI disclosure greeting and its audio — the first thing the
    customer hears (MVP section 8).
    """
    attempt = db.get(CallAttempt, payload.call_id)
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")
    try:
        conversation, speech = start_conversation(db, attempt)
    except ConversationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    db.refresh(conversation)

    return ReplyOut(
        conversation_id=conversation.id,
        reply=conversation.turns[0].text,
        language=conversation.language,
        intent="CONTINUE",
        service=conversation.service,
        state=conversation.state,
        collected=conversation.collected or {},
        missing_fields=missing_fields(conversation.service, conversation.collected or {}),
        audio_base64=base64.b64encode(speech.audio).decode() if speech.audio else "",
        audio_mime_type=speech.mime_type,
    )


@router.post("/conversations/{conversation_id}/turn", response_model=ReplyOut)
def customer_turn(
    conversation_id: int,
    payload: TurnRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Submit what the customer said and get the AI's reply."""
    conversation = _get_conversation(db, conversation_id)
    try:
        audio = payload.audio_bytes()
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    try:
        decision, speech = handle_turn(
            db, conversation, text=payload.text, audio=audio, actor=current_user
        )
    except ConversationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    db.refresh(conversation)

    return ReplyOut(
        conversation_id=conversation.id,
        reply=decision.reply,
        language=decision.language,
        intent=decision.intent,
        service=conversation.service,
        state=conversation.state,
        collected=conversation.collected or {},
        missing_fields=missing_fields(conversation.service, conversation.collected or {}),
        audio_base64=base64.b64encode(speech.audio).decode() if speech.audio else "",
        audio_mime_type=speech.mime_type,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    conversation = _get_conversation(db, conversation_id)
    detail = ConversationDetail(
        **ConversationOut.model_validate(conversation, from_attributes=True).model_dump(),
        turns=conversation.turns,
        structured_output=structured_output(conversation),
    )
    return detail


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    call_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Conversation).order_by(Conversation.id.desc())
    if call_id is not None:
        stmt = stmt.where(Conversation.call_attempt_id == call_id)
    return db.scalars(stmt.limit(min(limit, 200)).offset(offset)).all()
