"""user interests

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-06

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_interests",
        sa.Column("user_sub", sa.String(), primary_key=True),
        sa.Column("interest", sa.String(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("user_interests")
