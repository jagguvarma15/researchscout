"""index papers by the archives their categories span

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-04

Subjects and the scope rule both ask the same question -- which arXiv archives does this paper
touch, cross-lists included -- and neither the jsonb GIN index nor the expression index on
``split_part(primary_category, '.', 1)`` can answer it. The first matches whole category codes,
the second only the primary one.

A generated column would be the obvious answer and is not available: ``jsonb_array_elements_text``
is set-returning, which generated columns forbid. An IMMUTABLE function behind a GIN expression
index does the same job. Measured on the deployed corpus, "any math archive" goes from a
sequential scan to a bitmap index scan at 0.5 ms over 4,229 papers.

``STRICT`` matters: without it the function returns NULL for a NULL argument anyway, but saying
so lets the planner skip the call. ``PARALLEL SAFE`` lets it be used under parallel plans, which
the feed's count query can reach.

"""

from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION paper_archives(cats jsonb) RETURNS text[]
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
        AS $$
            SELECT array_agg(DISTINCT split_part(value, '.', 1))
            FROM jsonb_array_elements_text(cats)
        $$
        """
    )
    op.execute(
        "CREATE INDEX ix_papers_archives ON papers USING gin (paper_archives(categories))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_papers_archives")
    op.execute("DROP FUNCTION paper_archives(jsonb)")
