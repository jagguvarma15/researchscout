"""ask metrics v2: outcome, attribution, and cost columns

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-29

The original table recorded only completed answers, and its found flag meant different
things per mode. Every ask now lands a row - refusals, quota deaths, and busy rejections
included - so ``outcome`` becomes the one truth (ok, notfound, refused, llm_error, busy)
while ``found`` stays for old readers. The new columns carry what the answer actually was:
which model wrote it, what it cost in tokens, how long the first token took, how many
invented citations the post-check dropped, whether it was agentic or pinned or reranked,
and a short pseudonymous hash of who asked (the web's ownerTag derivation - never the sub
itself).

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

_COLUMNS = (
    sa.Column("model", sa.String(120), nullable=True),
    sa.Column("outcome", sa.String(12), nullable=True),
    sa.Column("user_hash", sa.String(16), nullable=True),
    sa.Column("agentic", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("pinned", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("rerank_used", sa.Boolean, nullable=True),
    sa.Column("prompt_tokens", sa.Integer, nullable=True),
    sa.Column("completion_tokens", sa.Integer, nullable=True),
    sa.Column("first_token_ms", sa.Integer, nullable=True),
    sa.Column("hallucinated", sa.Integer, nullable=True),
)


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("ask_metrics", column)
    # Legacy rows get a derived outcome so readers never branch on NULL for old data:
    # every pre-v2 row was a completed answer, so found maps cleanly.
    op.execute(
        "UPDATE ask_metrics SET outcome = CASE WHEN found THEN 'ok' ELSE 'notfound' END "
        "WHERE outcome IS NULL"
    )


def downgrade() -> None:
    for column in reversed(_COLUMNS):
        op.drop_column("ask_metrics", column.name)
