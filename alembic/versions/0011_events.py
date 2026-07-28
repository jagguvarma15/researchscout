"""interaction events

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-28

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_sub", sa.String(), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column(
            "paper_id",
            sa.String(),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("surface", sa.String(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_events_paper_event", "events", ["paper_id", "event"])


def downgrade() -> None:
    op.drop_index("ix_events_paper_event", table_name="events")
    op.drop_table("events")
