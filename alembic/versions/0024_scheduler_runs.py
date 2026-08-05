"""a ledger of scheduled runs

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-05

The deployment ran for two days on a stale image and a frozen environment, fetching nothing,
and the only place that fact existed was the container log. Whether the 05:00 slot actually
ran is something the database should be able to answer: one row per completed task run,
written by the scheduler around every task, read back by /v1/system/status, make
deploy-verify, and the Grafana ingest dashboard. Trimmed on write to the recent past, so it
stays a ledger rather than growing into a log store.

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduler_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("task", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ok", sa.Boolean, nullable=False),
        sa.Column("note", sa.String(length=400), nullable=False, server_default=""),
    )
    # The status endpoint reads newest-first; the dashboard groups by task; the trim deletes
    # by age. One index for recency, one for the per-task view.
    op.create_index("ix_scheduler_runs_finished", "scheduler_runs", ["finished_at"])
    op.create_index("ix_scheduler_runs_task_finished", "scheduler_runs", ["task", "finished_at"])


def downgrade() -> None:
    op.drop_index("ix_scheduler_runs_task_finished", table_name="scheduler_runs")
    op.drop_index("ix_scheduler_runs_finished", table_name="scheduler_runs")
    op.drop_table("scheduler_runs")
