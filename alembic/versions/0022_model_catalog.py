"""the model and benchmark catalogue

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-04

Three tables behind the Models and Benchmarks pages.

``ai_models`` is one row per model, keyed by a slug rather than an upstream identifier so the
two sources can meet in it: Epoch AI describes a model, Hugging Face knows how much it is
downloaded, and a model known to both is one row carrying both. ``paper_id`` is the join that
matters -- it is what lets a model link to the paper it came from, and a paper list the models
that came out of it. Nullable, because most models in the world have no paper in this corpus.

``benchmark_results`` deliberately keeps the model's *name* alongside its optional ``model_id``.
Of the models with benchmark scores, only about half are in the notable-models catalogue; a
strict foreign key would silently drop the rest, and a leaderboard missing half its rows is
worse than one whose rows do not all link.

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_models",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("organization", sa.Text(), nullable=True),
        sa.Column("publication_date", sa.Date(), nullable=True),
        # Comma-joined, matching how Epoch publishes it: a model is often several at once
        # ("Language,Multimodal,Vision").
        sa.Column("domains", sa.Text(), nullable=True),
        sa.Column("task", sa.Text(), nullable=True),
        sa.Column("parameters", sa.Float(), nullable=True),
        sa.Column("training_compute_flop", sa.Float(), nullable=True),
        sa.Column("accessibility", sa.Text(), nullable=True),
        sa.Column("open_weights", sa.Boolean(), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column(
            "paper_id", sa.String(), sa.ForeignKey("papers.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("hf_repo", sa.Text(), nullable=True),
        sa.Column("hf_downloads", sa.BigInteger(), nullable=True),
        sa.Column("hf_likes", sa.Integer(), nullable=True),
        sa.Column("sources", sa.Text(), nullable=False, server_default=""),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_models_published", "ai_models", [sa.text("publication_date DESC")])
    op.create_index("ix_ai_models_paper", "ai_models", ["paper_id"])
    op.create_index("ix_ai_models_organization", "ai_models", ["organization"])

    op.create_table(
        "benchmarks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("released_on", sa.Date(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "benchmark_results",
        sa.Column(
            "benchmark_id",
            sa.String(),
            sa.ForeignKey("benchmarks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("model_name", sa.Text(), primary_key=True),
        sa.Column(
            "model_id", sa.String(), sa.ForeignKey("ai_models.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("measured_on", sa.Date(), nullable=True),
        sa.Column("origin", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_benchmark_results_ranked",
        "benchmark_results",
        ["benchmark_id", sa.text("score DESC")],
    )
    op.create_index("ix_benchmark_results_model", "benchmark_results", ["model_id"])


def downgrade() -> None:
    op.drop_table("benchmark_results")
    op.drop_table("benchmarks")
    op.drop_table("ai_models")
