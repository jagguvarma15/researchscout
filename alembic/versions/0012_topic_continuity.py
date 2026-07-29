"""topic continuity

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-29

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "topics", sa.Column("topic_key", sa.String(), nullable=False, server_default="")
    )
    op.add_column("topics", sa.Column("trend", sa.String(), nullable=True))
    op.add_column(
        "topics", sa.Column("history", JSONB(), nullable=False, server_default=sa.text("'[]'"))
    )
    op.add_column("topics", sa.Column("centroid", JSONB(), nullable=True))
    op.add_column(
        "topics",
        sa.Column(
            "first_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_topics_key", "topics", ["topic_key"])


def downgrade() -> None:
    op.drop_index("ix_topics_key", table_name="topics")
    op.drop_column("topics", "first_seen")
    op.drop_column("topics", "centroid")
    op.drop_column("topics", "history")
    op.drop_column("topics", "trend")
    op.drop_column("topics", "topic_key")
