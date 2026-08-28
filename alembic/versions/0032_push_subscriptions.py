"""web push subscriptions, one row per browser endpoint

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-28

The endpoint URL is the primary key: a browser's push endpoint is globally unique, and a
re-subscribe from the same browser must replace its row rather than duplicate it. The
account owns its rows (CASCADE) so deleting an account takes its subscriptions with it.

"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("endpoint", sa.Text, nullable=False),
        sa.Column("user_sub", sa.String, nullable=False),
        sa.Column("keys", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_sub"], ["users.sub"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("endpoint"),
    )
    op.create_index("ix_push_subscriptions_user", "push_subscriptions", ["user_sub"])


def downgrade() -> None:
    op.drop_index("ix_push_subscriptions_user")
    op.drop_table("push_subscriptions")
