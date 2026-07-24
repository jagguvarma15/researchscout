"""topics

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-23

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "topics",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("papers", JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "built_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_topics_score", "topics", ["score"])


def downgrade() -> None:
    op.drop_index("ix_topics_score", table_name="topics")
    op.drop_table("topics")
