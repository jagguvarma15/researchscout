"""per-account site state, cached rather than kept

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-04

Four small things the site should remember for a signed-in visitor between page loads: what
they searched for, what they opened, what they pushed to the bottom of the feed, and how they
had the filters set.

All four are UNLOGGED. That is the point rather than a micro-optimisation: an unlogged table
writes no WAL, so recording a search costs about as little as it can while still being a row
somebody else's request can read -- and Postgres truncates these tables after an unclean stop,
which is exactly the durability a cache should have. Nothing here can be reconstructed and
nothing here matters if it is lost. The corpus, saved papers and interests stay logged.

Every table cascades from ``users``, so account deletion and the data export reach them without
either learning their names. The caps (twenty searches, twenty papers, two hundred dismissals)
are enforced on write in ``researchscout/store/account.py``: a cache with no ceiling is a table.

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

_TABLES = ("account_searches", "account_recent_papers", "account_dismissals", "account_filters")


def _user_sub() -> sa.Column[str]:
    return sa.Column(
        "user_sub",
        sa.String(),
        sa.ForeignKey("users.sub", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "account_searches",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        _user_sub(),
        sa.Column("query", sa.String(length=200), nullable=False),
        sa.Column("searched_at", sa.DateTime(timezone=True), nullable=False),
    )
    # One row per (account, query): searching the same phrase again moves it up the list
    # rather than filling the list with itself.
    op.create_unique_constraint(
        "uq_account_searches_query", "account_searches", ["user_sub", "query"]
    )
    op.create_index(
        "ix_account_searches_recent", "account_searches", ["user_sub", sa.text("searched_at DESC")]
    )

    op.create_table(
        "account_recent_papers",
        _user_sub(),
        sa.Column(
            "paper_id",
            sa.String(),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_sub", "paper_id"),
    )
    op.create_index(
        "ix_account_recent_papers_recent",
        "account_recent_papers",
        ["user_sub", sa.text("viewed_at DESC")],
    )

    op.create_table(
        "account_dismissals",
        _user_sub(),
        sa.Column(
            "paper_id",
            sa.String(),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_sub", "paper_id"),
    )
    op.create_index(
        "ix_account_dismissals_recent",
        "account_dismissals",
        ["user_sub", sa.text("dismissed_at DESC")],
    )

    op.create_table(
        "account_filters",
        _user_sub(),
        # The feed's query string verbatim, which is already the whole filter state - the
        # sidebar serializes into it and the server parses it back.
        sa.Column("query_string", sa.String(length=2000), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_sub"),
    )

    # SQLAlchemy has no argument for it, so the persistence is set after the fact. Same result:
    # these four write no WAL and are truncated after an unclean stop.
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} SET UNLOGGED")


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_table(table)
