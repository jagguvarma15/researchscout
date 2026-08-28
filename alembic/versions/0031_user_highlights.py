"""per-account reader highlights, synced across devices

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-28

The browser's localStorage copy stays the always-on original; this table is the flagged
sync target (RS_HIGHLIGHTS_SYNC) so marks survive a device change and the reader's
180-day local sweep. Keyed like the client stores them: one logical id per mark within a
paper, unique per account so the bulk replace is an upsert.

"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_highlights",
        sa.Column("user_sub", sa.String, nullable=False),
        sa.Column("paper_id", sa.String, nullable=False),
        sa.Column("highlight_id", sa.String(64), nullable=False),
        sa.Column("page", sa.Integer, nullable=False),
        sa.Column("color", sa.String(32), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("rects", JSONB, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_sub"], ["users.sub"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_sub", "paper_id", "highlight_id"),
    )


def downgrade() -> None:
    op.drop_table("user_highlights")
