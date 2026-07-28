"""citation edges

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-28

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "citation_edges",
        sa.Column(
            "citing_id",
            sa.String(),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("cited_arxiv", sa.String(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_citation_edges_cited", "citation_edges", ["cited_arxiv"])
    op.create_table(
        "citation_fetches",
        sa.Column(
            "citing_id",
            sa.String(),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("citation_fetches")
    op.drop_index("ix_citation_edges_cited", table_name="citation_edges")
    op.drop_table("citation_edges")
