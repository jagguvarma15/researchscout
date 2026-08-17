"""citation watermarks, in-flight ledger rows, raw-item pruning index

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-17

The citation walker refreshes papers in order of how stale their citation coverage is, so it
needs one row per paper saying when citations were last fetched and by which source - the
watermark is the cursor, and an interrupted walk resumes wherever coverage is thinnest.

scheduler_runs.finished_at becomes nullable so a task can write its row on start and fill the
finish in later: a hung or killed task now leaves visible evidence instead of nothing.

raw_items gains an index on fetched_at because the new retention prune deletes by age.

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "citation_refreshes",
        sa.Column(
            "paper_id",
            sa.String(),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_citation_refreshes_fetched", "citation_refreshes", ["fetched_at"])
    op.alter_column("scheduler_runs", "finished_at", nullable=True)
    op.create_index("ix_raw_items_fetched", "raw_items", ["fetched_at"])


def downgrade() -> None:
    op.drop_index("ix_raw_items_fetched", table_name="raw_items")
    op.execute("DELETE FROM scheduler_runs WHERE finished_at IS NULL")
    op.alter_column("scheduler_runs", "finished_at", nullable=False)
    op.drop_index("ix_citation_refreshes_fetched", table_name="citation_refreshes")
    op.drop_table("citation_refreshes")
