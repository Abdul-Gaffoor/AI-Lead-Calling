"""call recordings and quality review

Revision ID: c8a7e2d14f60
Revises: b4f21a9c73e5
Create Date: 2026-09-20 11:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c8a7e2d14f60'
down_revision: Union[str, None] = 'b4f21a9c73e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# New to this revision, so these enums are created here — unlike service_type,
# which is shared and must never be re-created (see database/migrations/env.py).
NEW_ENUM_TYPES = ('review_verdict',)


def upgrade() -> None:
    op.create_table(
        'call_recordings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('call_attempt_id', sa.Integer(), nullable=False),
        sa.Column('storage_key', sa.String(length=300), nullable=False),
        sa.Column('content_type', sa.String(length=80), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('source_url', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['call_attempt_id'], ['call_attempts.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_call_recordings_call_attempt_id'), 'call_recordings',
                    ['call_attempt_id'], unique=True)

    op.create_table(
        'call_reviews',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('call_attempt_id', sa.Integer(), nullable=False),
        sa.Column('reviewer_id', sa.Integer(), nullable=False),
        sa.Column('verdict', sa.Enum('CORRECT', 'INCORRECT', name='review_verdict'),
                  nullable=False),
        sa.Column('flags', sa.JSON(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['call_attempt_id'], ['call_attempts.id'], ),
        sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('call_attempt_id', 'reviewer_id', name='uq_review_per_reviewer'),
    )
    op.create_index(op.f('ix_call_reviews_call_attempt_id'), 'call_reviews',
                    ['call_attempt_id'], unique=False)
    op.create_index(op.f('ix_call_reviews_reviewer_id'), 'call_reviews',
                    ['reviewer_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_call_reviews_reviewer_id'), table_name='call_reviews')
    op.drop_index(op.f('ix_call_reviews_call_attempt_id'), table_name='call_reviews')
    op.drop_table('call_reviews')
    op.drop_index(op.f('ix_call_recordings_call_attempt_id'), table_name='call_recordings')
    op.drop_table('call_recordings')

    # Enums this revision created are database-wide on PostgreSQL and outlive
    # their table, so a re-apply would hit "type already exists" without this.
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        for name in NEW_ENUM_TYPES:
            sa.Enum(name=name).drop(bind, checkfirst=True)
