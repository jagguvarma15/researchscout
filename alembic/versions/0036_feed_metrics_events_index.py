"""feed metrics table and hot-path indexes

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-03

The For You feed grew request-time latency instrumentation, so it needs a place to record it -
feed_metrics, the ask_metrics analog. The events index closes the standing gap the latency audit
found: events was the fastest-growing table with no index on user_sub, yet every feed read and
the new prune filter it first. (papers.published_at is already covered by ix_papers_published_at
from migration 0009.)

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feed_metrics",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("user_hash", sa.String(16), nullable=True),
        sa.Column("days", sa.Integer, nullable=False),
        sa.Column("k", sa.Integer, nullable=False),
        sa.Column("centroids", sa.Integer, nullable=False),
        sa.Column("candidates", sa.Integer, nullable=False),
        sa.Column("returned", sa.Integer, nullable=False),
        sa.Column("profile_cache_hit", sa.Boolean, nullable=False),
        sa.Column("profile_ms", sa.Integer, nullable=True),
        sa.Column("search_ms", sa.Integer, nullable=True),
        sa.Column("signals_ms", sa.Integer, nullable=True),
        sa.Column("rank_ms", sa.Integer, nullable=True),
        sa.Column("total_ms", sa.Integer, nullable=False),
    )
    op.create_index("ix_feed_metrics_requested_at", "feed_metrics", ["requested_at"])
    op.create_index("ix_events_user", "events", ["user_sub", "event", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_events_user", table_name="events")
    op.drop_index("ix_feed_metrics_requested_at", table_name="feed_metrics")
    op.drop_table("feed_metrics")
