"""record what scale a benchmark's scores are on

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-05

Benchmark scores are not all fractions, and the page was multiplying every one of them by a
hundred. Most are accuracies between zero and one, so that reads correctly for them and is
nonsense for the rest: OS World is a percentage already (3 to 72), Ale Bench is a score in the
thousands, Algotune is a speedup ratio between 1 and 2, and Vending Bench is dollars, which go
negative. Eleven of the seventy-three benchmarks in the hub are like this.

Deciding at render time from whatever rows happen to be on screen would let the same benchmark
format differently on the leaderboard and in the provider comparison, so the scale is settled
once when the scores are written, over the whole set, and stored beside the benchmark.

"fraction" rather than a boolean because there is an obvious third case waiting: a benchmark
already expressed in percentage points, which wants neither multiplying nor a bare number.

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "benchmarks",
        sa.Column("score_scale", sa.String(length=16), nullable=False, server_default="fraction"),
    )


def downgrade() -> None:
    op.drop_column("benchmarks", "score_scale")
