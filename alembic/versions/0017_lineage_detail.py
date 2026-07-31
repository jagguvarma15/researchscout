"""lineage detail column and stage-entered index

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-31

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipeline_lineage", sa.Column("detail", postgresql.JSONB(), nullable=True))
    # The queue-wait and end-to-end dashboard queries filter one stage by entered_at and
    # self-join on the primary key; this index makes them index-driven.
    op.create_index(
        "ix_pipeline_lineage_stage_entered", "pipeline_lineage", ["stage", "entered_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_lineage_stage_entered", table_name="pipeline_lineage")
    op.drop_column("pipeline_lineage", "detail")
