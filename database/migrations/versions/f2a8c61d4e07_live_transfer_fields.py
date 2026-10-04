"""Live transfer: executive phone and availability (MVP section 23).

A call cannot be handed to an executive without a number to hand it to,
and "may sign in" is not the same question as "is at their desk right
now". Both default to off, so nobody starts receiving transferred calls
because of a deploy.
"""

import sqlalchemy as sa
from alembic import op

revision = "f2a8c61d4e07"
down_revision = "e1f4a7b29c33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.String(length=20), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "available_for_transfer",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column("users", "available_for_transfer", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "available_for_transfer")
    op.drop_column("users", "phone")
