"""MFA for administrators (MVP section 32).

Three columns on `users`. All nullable or defaulted, so the migration is
safe on a live table and existing sessions are unaffected.

An administrator who has not enrolled is not locked out by this: the login
endpoint hands them a token scoped to enrolment only. See
`backend/auth/router.py`.
"""

import sqlalchemy as sa
from alembic import op

revision = "e1f4a7b29c33"
down_revision = "c8a7e2d14f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mfa_secret", sa.String(length=64), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "mfa_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("users", sa.Column("mfa_recovery_codes", sa.JSON(), nullable=True))
    # The default was only needed to backfill the existing rows; leaving it
    # on would let application bugs write a row with no explicit value.
    op.alter_column("users", "mfa_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "mfa_recovery_codes")
    op.drop_column("users", "mfa_enabled")
    op.drop_column("users", "mfa_secret")
