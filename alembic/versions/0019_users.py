"""users table and cascade from user-scoped rows

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-01

Identity used to be a bare ``sub`` string on three tables with nothing behind it. A public
site needs an account: the terms version its owner accepted, the name to show, and a single
place to delete when they ask. The foreign keys make that deletion one statement instead of a
list of tables somebody will forget to extend.

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

_SCOPED_TABLES = ("saved_papers", "user_interests", "events")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("sub", sa.String(), primary_key=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("tos_version", sa.String(), nullable=True),
        sa.Column("tos_accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The built-in local user always exists, so a no-auth install keeps working without any
    # account bookkeeping; every sub already referenced by existing rows is adopted so the
    # foreign keys below validate against real data.
    op.execute("INSERT INTO users (sub) VALUES ('local') ON CONFLICT (sub) DO NOTHING")
    op.execute(
        """
        INSERT INTO users (sub)
        SELECT user_sub FROM saved_papers
        UNION SELECT user_sub FROM user_interests
        UNION SELECT user_sub FROM events
        ON CONFLICT (sub) DO NOTHING
        """
    )
    for table in _SCOPED_TABLES:
        op.create_foreign_key(
            f"fk_{table}_user_sub_users",
            table,
            "users",
            ["user_sub"],
            ["sub"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    for table in _SCOPED_TABLES:
        op.drop_constraint(f"fk_{table}_user_sub_users", table, type_="foreignkey")
    op.drop_table("users")
