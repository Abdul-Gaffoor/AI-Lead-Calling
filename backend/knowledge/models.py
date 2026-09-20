"""Curated Swaraj knowledge base (MVP section 18).

Two tables in the existing PostgreSQL database rather than a second datastore:
one document per approved piece of company content, and the chunks of it that
carry embeddings.

Nothing here is served to a customer until a person approves it. The MVP is
explicit that only approved company information enters production RAG, and the
seeded content is drafted from the MVP document, not from Swaraj's own
approved material.
"""

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from backend.core.database import Base
from backend.leads.models import ServiceType

#: The knowledge areas from MVP section 18. A plain string with a checked
#: vocabulary rather than a database enum: these change as Swaraj adds
#: material, and altering a PostgreSQL enum in a migration is a trap this
#: codebase has already been bitten by once.
CATEGORIES = (
    "company",
    "residential",
    "commercial",
    "industrial",
    "agriculture",
    "ground-mounted",
    "pm-surya-ghar",
    "on-grid",
    "hybrid",
    "off-grid",
    "panels",
    "inverters",
    "warranty",
    "subsidy",
    "financing",
    "installation",
    "net-metering",
    "maintenance",
    "faq",
)


class EmbeddingVector(TypeDecorator):
    """A vector column: pgvector on PostgreSQL, JSON on SQLite.

    Declared without a fixed dimension so the mock provider (256) and a real
    one (1024) can both be stored — chunks record which model produced them
    and search only ever compares within one model. Adding an ANN index later
    means pinning a dimension; at the size of a curated company knowledge base
    an exact scan is well inside the latency a phone call can absorb.
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector())
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return [float(item) for item in value]

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return [float(item) for item in value]


class KnowledgeDocument(Base):
    """One piece of Swaraj content, e.g. "How net metering works"."""

    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    #: Narrows retrieval when the conversation already knows the service.
    service: Mapped[ServiceType | None] = mapped_column(
        Enum(ServiceType, name="service_type"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: Where the content came from, so an approver can check it.
    source: Mapped[str | None] = mapped_column(String(300), nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)

    #: The gate in MVP section 18. Unapproved documents are never retrieved.
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    chunks = relationship(
        "KnowledgeChunk",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="KnowledgeChunk.ordinal",
    )


class KnowledgeChunk(Base):
    """A retrievable passage of a document, with its embedding."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (UniqueConstraint("document_id", "ordinal", name="uq_chunk_ordinal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingVector, nullable=True)
    #: Vectors from different models are not comparable, so search filters on
    #: this. A provider change leaves old chunks in place but unused until
    #: they are re-embedded.
    embedding_model: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document = relationship("KnowledgeDocument", back_populates="chunks")
