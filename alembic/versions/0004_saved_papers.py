"""saved papers

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-06

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_papers",
        sa.Column("user_sub", sa.String(), primary_key=True),
        sa.Column(
            "paper_id",
            sa.String(),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "saved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("saved_papers")
