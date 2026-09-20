"""Ingest, approval and retrieval for the knowledge base (MVP section 18)."""

import logging
from dataclasses import dataclass

from sqlalchemy import select, text as sql_text
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.knowledge.chunking import chunk_document
from backend.knowledge.embeddings import EmbeddingError, cosine_similarity
from backend.knowledge.factory import get_embedding_provider
from backend.knowledge.models import CATEGORIES, KnowledgeChunk, KnowledgeDocument
from backend.leads.models import ServiceType

logger = logging.getLogger(__name__)


class KnowledgeError(Exception):
    """Raised when a document cannot be stored or indexed."""


@dataclass
class Passage:
    """One retrieved passage, with enough provenance to cite or audit it."""

    chunk_id: int
    document_id: int
    slug: str
    title: str
    category: str
    text: str
    similarity: float
    source: str | None = None


def _utcnow():
    from backend.calls.service import utcnow

    return utcnow()


# --- ingest -----------------------------------------------------------------


def upsert_document(
    db: Session,
    *,
    slug: str,
    title: str,
    category: str,
    body: str,
    service: ServiceType | None = None,
    source: str | None = None,
    language: str = "en",
) -> tuple[KnowledgeDocument, bool]:
    """Create or update a document. Returns (document, created)."""
    if category not in CATEGORIES:
        raise KnowledgeError(f"Unknown category {category!r}; expected one of {', '.join(CATEGORIES)}")
    if not body.strip():
        raise KnowledgeError("A knowledge document cannot be empty")

    document = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.slug == slug))
    created = document is None

    if created:
        document = KnowledgeDocument(slug=slug)
        db.add(document)

    body_changed = not created and document.body != body

    document.title = title
    document.category = category
    document.service = service
    document.body = body
    document.source = source
    document.language = language

    if body_changed:
        # Editing the text of an approved document withdraws that approval.
        # Approval is a person vouching for specific words; if the words
        # change, nobody has vouched for the new ones (MVP section 18).
        document.is_approved = False
        document.approved_by_id = None
        document.approved_at = None

    db.flush()
    if created or body_changed:
        rebuild_chunks(db, document)
    return document, created


def rebuild_chunks(db: Session, document: KnowledgeDocument) -> int:
    """Re-split and re-embed a document. Returns the number of chunks."""
    texts = chunk_document(document.title, document.body)
    if not texts:
        raise KnowledgeError(f"Document {document.slug!r} produced no passages")

    provider = get_embedding_provider()
    try:
        vectors = provider.embed_documents(texts)
    except EmbeddingError:
        raise
    except Exception as exc:
        raise EmbeddingError(f"Embedding failed for {document.slug!r}: {exc}") from exc

    if len(vectors) != len(texts):
        raise EmbeddingError(
            f"Embedding provider returned {len(vectors)} vectors for {len(texts)} passages"
        )

    for chunk in list(document.chunks):
        db.delete(chunk)
    db.flush()

    for ordinal, (chunk_text, vector) in enumerate(zip(texts, vectors)):
        db.add(
            KnowledgeChunk(
                document_id=document.id,
                ordinal=ordinal,
                text=chunk_text,
                embedding=vector,
                embedding_model=provider.model,
            )
        )
    db.flush()
    return len(texts)


def reindex(db: Session) -> dict:
    """Re-embed every document whose chunks came from another model.

    Switching embedding provider leaves the old vectors in place but unused —
    search filters on the active model — so nothing is retrieved until this
    runs. That is the safe direction: stale vectors silently degrade answers.
    """
    provider = get_embedding_provider()
    documents = db.scalars(select(KnowledgeDocument)).all()

    rebuilt = 0
    passages = 0
    for document in documents:
        models = {chunk.embedding_model for chunk in document.chunks}
        if models == {provider.model} and document.chunks:
            continue
        passages += rebuild_chunks(db, document)
        rebuilt += 1

    return {"documents": len(documents), "reindexed": rebuilt, "passages": passages,
            "model": provider.model}


def approve(db: Session, document: KnowledgeDocument, approver_id: int) -> KnowledgeDocument:
    document.is_approved = True
    document.approved_by_id = approver_id
    document.approved_at = _utcnow()
    db.flush()
    return document


def withdraw_approval(db: Session, document: KnowledgeDocument) -> KnowledgeDocument:
    document.is_approved = False
    document.approved_by_id = None
    document.approved_at = None
    db.flush()
    return document


# --- retrieval ---------------------------------------------------------------


def search(
    db: Session,
    query: str,
    *,
    service: ServiceType | None = None,
    category: str | None = None,
    limit: int | None = None,
    min_similarity: float | None = None,
) -> list[Passage]:
    """Approved passages most similar to `query`, best first.

    Only approved documents, and only chunks embedded by the active model —
    vectors from two different models are not comparable, and ranking across
    them returns confident nonsense.
    """
    query = (query or "").strip()
    if not query:
        return []

    limit = limit or settings.knowledge_top_k
    floor = settings.knowledge_min_similarity if min_similarity is None else min_similarity
    provider = get_embedding_provider()
    vector = provider.embed_query(query)

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        hits = _search_pgvector(db, vector, provider.model, service, category, limit)
    else:
        hits = _search_python(db, vector, provider.model, service, category, limit)

    return [hit for hit in hits if hit.similarity >= floor]


def _filters(service: ServiceType | None, category: str | None) -> tuple[str, dict]:
    """SQL fragment and parameters shared by both search paths."""
    clauses = []
    params: dict = {}
    if service is not None:
        # A document with no service is general guidance and always applies.
        clauses.append("(d.service IS NULL OR d.service = :service)")
        params["service"] = service.value
    if category is not None:
        clauses.append("d.category = :category")
        params["category"] = category
    return ("".join(f" AND {clause}" for clause in clauses), params)


def _search_pgvector(
    db: Session,
    vector: list[float],
    model: str,
    service: ServiceType | None,
    category: str | None,
    limit: int,
) -> list[Passage]:
    """Rank in the database. `<=>` is cosine distance, so similarity is 1 - it."""
    where, params = _filters(service, category)
    params.update(
        {
            "q": "[" + ",".join(repr(float(value)) for value in vector) + "]",
            "model": model,
            "limit": limit,
        }
    )
    rows = db.execute(
        sql_text(
            f"""
            SELECT c.id, c.document_id, d.slug, d.title, d.category, c.text, d.source,
                   1 - (c.embedding <=> CAST(:q AS vector)) AS similarity
            FROM knowledge_chunks c
            JOIN knowledge_documents d ON d.id = c.document_id
            WHERE d.is_approved IS TRUE
              AND c.embedding IS NOT NULL
              AND c.embedding_model = :model
              {where}
            ORDER BY c.embedding <=> CAST(:q AS vector)
            LIMIT :limit
            """
        ),
        params,
    ).all()

    return [
        Passage(
            chunk_id=row[0], document_id=row[1], slug=row[2], title=row[3],
            category=row[4], text=row[5], source=row[6], similarity=float(row[7]),
        )
        for row in rows
    ]


def _search_python(
    db: Session,
    vector: list[float],
    model: str,
    service: ServiceType | None,
    category: str | None,
    limit: int,
) -> list[Passage]:
    """Rank in Python. Used on SQLite, which has no vector operators.

    An exact scan over a curated knowledge base is a few hundred comparisons.
    """
    statement = (
        select(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .where(
            KnowledgeDocument.is_approved.is_(True),
            KnowledgeChunk.embedding_model == model,
            KnowledgeChunk.embedding.is_not(None),
        )
    )
    if service is not None:
        statement = statement.where(
            (KnowledgeDocument.service.is_(None)) | (KnowledgeDocument.service == service)
        )
    if category is not None:
        statement = statement.where(KnowledgeDocument.category == category)

    scored = [
        Passage(
            chunk_id=chunk.id, document_id=document.id, slug=document.slug,
            title=document.title, category=document.category, text=chunk.text,
            source=document.source,
            similarity=cosine_similarity(vector, chunk.embedding or []),
        )
        for chunk, document in db.execute(statement).all()
    ]
    scored.sort(key=lambda passage: passage.similarity, reverse=True)
    return scored[:limit]
