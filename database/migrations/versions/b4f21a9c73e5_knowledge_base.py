"""knowledge base with pgvector

Revision ID: b4f21a9c73e5
Revises: a91ed86ed698
Create Date: 2026-09-20 09:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

SERVICE_TYPE_VALUES = (
    'RESIDENTIAL_SOLAR', 'PM_SURYA_GHAR', 'COMMERCIAL_SOLAR', 'INDUSTRIAL_SOLAR',
    'AGRICULTURE_SOLAR', 'GROUND_MOUNTED_SOLAR', 'EXISTING_SOLAR_UPGRADE',
    'SOLAR_MAINTENANCE', 'PANEL_CLEANING', 'GENERAL_ENQUIRY',
)


def _service_type():
    """Reuse the existing service_type enum rather than re-creating it.

    On PostgreSQL an enum is a database-wide object; a second CREATE TYPE
    fails. The initial migration owns this type.
    """
    if op.get_bind().dialect.name == 'postgresql':
        return postgresql.ENUM(*SERVICE_TYPE_VALUES, name='service_type', create_type=False)
    return sa.Enum(*SERVICE_TYPE_VALUES, name='service_type')


def _embedding_column():
    """pgvector on PostgreSQL, JSON on SQLite (tests).

    Declared without a dimension so the mock provider (256) and a real one
    (1024) both fit; chunks record which model produced them and search only
    compares within one model.
    """
    if op.get_bind().dialect.name == 'postgresql':
        from pgvector.sqlalchemy import Vector

        return Vector()
    return sa.JSON()


revision: str = 'b4f21a9c73e5'
down_revision: Union[str, None] = 'a91ed86ed698'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        # Needs the pgvector extension: the production and CI images are
        # pgvector/pgvector:pg16 rather than plain postgres:16.
        op.execute('CREATE EXTENSION IF NOT EXISTS vector')

    op.create_table(
        'knowledge_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('slug', sa.String(length=120), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('category', sa.String(length=40), nullable=False),
        sa.Column('service', _service_type(), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=300), nullable=True),
        sa.Column('language', sa.String(length=10), nullable=False),
        sa.Column('is_approved', sa.Boolean(), nullable=False),
        sa.Column('approved_by_id', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['approved_by_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_knowledge_documents_slug'), 'knowledge_documents',
                    ['slug'], unique=True)
    op.create_index(op.f('ix_knowledge_documents_category'), 'knowledge_documents',
                    ['category'], unique=False)

    op.create_table(
        'knowledge_chunks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('embedding', _embedding_column(), nullable=True),
        sa.Column('embedding_model', sa.String(length=80), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['knowledge_documents.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_id', 'ordinal', name='uq_chunk_ordinal'),
    )
    op.create_index(op.f('ix_knowledge_chunks_document_id'), 'knowledge_chunks',
                    ['document_id'], unique=False)
    op.create_index(op.f('ix_knowledge_chunks_embedding_model'), 'knowledge_chunks',
                    ['embedding_model'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_knowledge_chunks_embedding_model'), table_name='knowledge_chunks')
    op.drop_index(op.f('ix_knowledge_chunks_document_id'), table_name='knowledge_chunks')
    op.drop_table('knowledge_chunks')
    op.drop_index(op.f('ix_knowledge_documents_category'), table_name='knowledge_documents')
    op.drop_index(op.f('ix_knowledge_documents_slug'), table_name='knowledge_documents')
    op.drop_table('knowledge_documents')
    # The vector extension is left in place: dropping it would break any other
    # vector column, and it is harmless when unused.
