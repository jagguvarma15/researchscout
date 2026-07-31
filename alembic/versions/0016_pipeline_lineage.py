"""pipeline lineage table and hourly rollup view

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-30

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_lineage",
        sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("stage", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        # No FK: a packet can fail before its paper exists.
        sa.Column("paper_id", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_pipeline_lineage_exited", "pipeline_lineage", ["exited_at"])
    op.create_index("ix_pipeline_lineage_stage_outcome", "pipeline_lineage", ["stage", "outcome"])
    # Grafana-ready rollups; the API computes the same aggregate directly on the table.
    op.execute(
        "CREATE VIEW pipeline_rollups_hourly AS "
        "SELECT date_trunc('hour', coalesce(exited_at, entered_at)) AS bucket, "
        "stage, kind, source, outcome, category, "
        "count(*) AS packets, "
        "avg(extract(epoch FROM exited_at - entered_at)) AS avg_seconds "
        "FROM pipeline_lineage GROUP BY 1, 2, 3, 4, 5, 6"
    )


def downgrade() -> None:
    op.execute("DROP VIEW pipeline_rollups_hourly")
    op.drop_index("ix_pipeline_lineage_stage_outcome", table_name="pipeline_lineage")
    op.drop_index("ix_pipeline_lineage_exited", table_name="pipeline_lineage")
    op.drop_table("pipeline_lineage")
