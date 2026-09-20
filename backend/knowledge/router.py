from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, require_roles
from backend.auth.models import Role, User
from backend.core.database import get_db
from backend.knowledge import service as knowledge_service
from backend.knowledge.embeddings import EmbeddingError
from backend.knowledge.models import CATEGORIES, KnowledgeChunk, KnowledgeDocument
from backend.knowledge.schemas import (
    DocumentDetail,
    DocumentIn,
    DocumentOut,
    PassageOut,
    ReindexResult,
    SearchRequest,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

#: Writing and approving company content is an admin/manager action. The MVP
#: makes approval the gate on what a customer can be told, so it is not open
#: to whoever can upload a lead list.
_curator = require_roles(Role.SUPER_ADMIN, Role.SALES_MANAGER)


def _detail(db: Session, document: KnowledgeDocument) -> dict:
    passages = db.scalar(
        select(func.count(KnowledgeChunk.id)).where(KnowledgeChunk.document_id == document.id)
    )
    data = DocumentDetail.model_validate(document).model_dump()
    data["passages"] = passages or 0
    return data


@router.get("/categories", response_model=list[str])
def categories(_: User = Depends(get_current_user)):
    return list(CATEGORIES)


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(
    category: str | None = None,
    approved: bool | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    statement = select(KnowledgeDocument).order_by(
        KnowledgeDocument.category, KnowledgeDocument.title
    )
    if category is not None:
        statement = statement.where(KnowledgeDocument.category == category)
    if approved is not None:
        statement = statement.where(KnowledgeDocument.is_approved.is_(approved))
    return db.scalars(statement).all()


@router.get("/documents/{slug}", response_model=DocumentDetail)
def get_document(
    slug: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    document = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.slug == slug))
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such document")
    return _detail(db, document)


@router.put("/documents/{slug}", response_model=DocumentDetail)
def put_document(
    slug: str,
    payload: DocumentIn,
    db: Session = Depends(get_db),
    _: User = Depends(_curator),
):
    if payload.slug != slug:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Slug in the body must match the URL")
    try:
        document, _created = knowledge_service.upsert_document(
            db,
            slug=slug,
            title=payload.title,
            category=payload.category,
            body=payload.body,
            service=payload.service,
            source=payload.source,
            language=payload.language,
        )
    except EmbeddingError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    except knowledge_service.KnowledgeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    detail = _detail(db, document)
    db.commit()
    return detail


@router.post("/documents/{slug}/approve", response_model=DocumentOut)
def approve_document(
    slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(_curator),
):
    """Let this document be quoted to customers. Editing it withdraws approval."""
    document = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.slug == slug))
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such document")
    if not document.chunks:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Document has no indexed passages; re-save it before approving",
        )
    knowledge_service.approve(db, document, user.id)
    db.commit()
    db.refresh(document)
    return document


@router.post("/documents/{slug}/withdraw", response_model=DocumentOut)
def withdraw_document(
    slug: str,
    db: Session = Depends(get_db),
    _: User = Depends(_curator),
):
    document = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.slug == slug))
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such document")
    knowledge_service.withdraw_approval(db, document)
    db.commit()
    db.refresh(document)
    return document


@router.delete("/documents/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    slug: str,
    db: Session = Depends(get_db),
    _: User = Depends(_curator),
):
    document = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.slug == slug))
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such document")
    db.delete(document)
    db.commit()


@router.post("/search", response_model=list[PassageOut])
def search(
    payload: SearchRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """What the AI would be given for this question. Useful for reviewing
    answer quality without placing a call."""
    try:
        passages = knowledge_service.search(
            db,
            payload.query,
            service=payload.service,
            category=payload.category,
            limit=payload.limit,
        )
    except EmbeddingError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return [asdict(passage) for passage in passages]


@router.post("/reindex", response_model=ReindexResult)
def reindex(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Role.SUPER_ADMIN)),
):
    """Re-embed documents after an embedding provider change."""
    try:
        result = knowledge_service.reindex(db)
    except EmbeddingError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    db.commit()
    return result
