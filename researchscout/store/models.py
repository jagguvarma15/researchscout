"""ORM models: papers, external-id map, raw landing, ingest cursors, embeddings, and signals."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import HALFVEC, Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from researchscout.store.db import Base


class PaperRow(Base):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str] = mapped_column(Text)
    authors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # author_names is a generated column (migration 0009) left unmapped, like search_tsv.
    categories: Mapped[list[str]] = mapped_column(JSONB, default=list)
    primary_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    venue: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    citation_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    sections: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    labels: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    row_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ExternalIdRow(Base):
    __tablename__ = "paper_external_ids"

    scheme: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, primary_key=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)


class RawItemRow(Base):
    __tablename__ = "raw_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class IngestStateRow(Base):
    __tablename__ = "ingest_state"

    source: Mapped[str] = mapped_column(String, primary_key=True)
    cursor: Mapped[str | None] = mapped_column(String, nullable=True)
    last_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PaperEmbeddingRow(Base):
    __tablename__ = "paper_embeddings"

    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    model_id: Mapped[str] = mapped_column(String, primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(384))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaperChunkRow(Base):
    __tablename__ = "paper_chunks"
    __table_args__ = (Index("ix_paper_chunks_paper", "paper_id", "model_id", "chunk_index"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    model_id: Mapped[str] = mapped_column(String)
    chunk_index: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    # halfvec halves the RAM of the chunk index — the difference between chunk-level search
    # fitting on 8GB after a deep backfill and not.
    embedding: Mapped[list[float]] = mapped_column(HALFVEC(384))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DigestRow(Base):
    __tablename__ = "digests"

    slug: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    body: Mapped[str] = mapped_column(Text)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TopicRow(Base):
    __tablename__ = "topics"
    __table_args__ = (Index("ix_topics_key", "topic_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Stable identity across the wholesale rebuilds: carried from the most similar previous
    # topic (centroid cosine match), so size history and trends survive.
    topic_key: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    label: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float] = mapped_column(Float)
    size: Mapped[int] = mapped_column(Integer)
    papers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # new | rising | steady | fading, from the size history.
    trend: Mapped[str | None] = mapped_column(String, nullable=True)
    # [{"built_at": iso, "size": int}, ...] oldest first, capped.
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    centroid: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserRow(Base):
    """An account. Identity still comes from the token's ``sub``; this row is what the site
    knows about it - the terms version accepted, the name to show, when it was last seen."""

    __tablename__ = "users"

    sub: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # A slug naming one of the web app's drawn avatars; the art and the valid set live there.
    avatar: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    tos_version: Mapped[str | None] = mapped_column(String, nullable=True)
    tos_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SavedPaperRow(Base):
    __tablename__ = "saved_papers"

    user_sub: Mapped[str] = mapped_column(
        ForeignKey("users.sub", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserInterestRow(Base):
    __tablename__ = "user_interests"

    user_sub: Mapped[str] = mapped_column(
        ForeignKey("users.sub", ondelete="CASCADE"), primary_key=True
    )
    interest: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiModelRow(Base):
    """One AI model in the landscape, as the two upstreams jointly describe it.

    Keyed by a slug of the name rather than an upstream identifier, so a model Epoch AI
    describes and Hugging Face counts downloads for is one row carrying both. ``paper_id`` is
    the join this site exists to make; it is null for most models, which have no paper here.
    """

    __tablename__ = "ai_models"
    __table_args__ = (
        Index("ix_ai_models_published", "publication_date"),
        Index("ix_ai_models_paper", "paper_id"),
        Index("ix_ai_models_organization", "organization"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    organization: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    domains: Mapped[str | None] = mapped_column(Text, nullable=True)
    task: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameters: Mapped[float | None] = mapped_column(Float, nullable=True)
    training_compute_flop: Mapped[float | None] = mapped_column(Float, nullable=True)
    accessibility: Mapped[str | None] = mapped_column(Text, nullable=True)
    open_weights: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("papers.id", ondelete="SET NULL"), nullable=True
    )
    hf_repo: Mapped[str | None] = mapped_column(Text, nullable=True)
    hf_downloads: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hf_likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sources: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BenchmarkRow(Base):
    """One benchmark, with how many model scores are recorded against it."""

    __tablename__ = "benchmarks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    released_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: "fraction" when every score sits in [0, 1] and reads as a percentage, "raw" otherwise -
    #: a ratio, an Elo, an amount of money. Settled once over the whole score set at refresh
    #: (migration 0023) so two pages showing the same benchmark cannot format it differently.
    score_scale: Mapped[str] = mapped_column(String(16), nullable=False, server_default="fraction")
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BenchmarkResultRow(Base):
    """One model's score on one benchmark.

    ``model_name`` is part of the key and ``model_id`` is optional: only about half the
    benchmarked models are in the notable-models catalogue, and a leaderboard missing half its
    rows would be worse than one whose rows do not all link.
    """

    __tablename__ = "benchmark_results"
    __table_args__ = (
        Index("ix_benchmark_results_ranked", "benchmark_id", "score"),
        Index("ix_benchmark_results_model", "model_id"),
    )

    benchmark_id: Mapped[str] = mapped_column(
        ForeignKey("benchmarks.id", ondelete="CASCADE"), primary_key=True
    )
    model_name: Mapped[str] = mapped_column(Text, primary_key=True)
    model_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_models.id", ondelete="SET NULL"), nullable=True
    )
    score: Mapped[float] = mapped_column(Float)
    measured_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    origin: Mapped[str | None] = mapped_column(Text, nullable=True)


class AccountSearchRow(Base):
    """A phrase this account searched for. Unlogged (migration 0021): a cache, not a record."""

    __tablename__ = "account_searches"
    __table_args__ = (
        Index("uq_account_searches_query", "user_sub", "query", unique=True),
        Index("ix_account_searches_recent", "user_sub", "searched_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_sub: Mapped[str] = mapped_column(ForeignKey("users.sub", ondelete="CASCADE"))
    query: Mapped[str] = mapped_column(String(200))
    searched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AccountRecentPaperRow(Base):
    """A paper this account opened, for the continue-reading strip. Unlogged."""

    __tablename__ = "account_recent_papers"
    __table_args__ = (Index("ix_account_recent_papers_recent", "user_sub", "viewed_at"),)

    user_sub: Mapped[str] = mapped_column(
        ForeignKey("users.sub", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AccountDismissalRow(Base):
    """A paper this account pushed to the end of the feed. Unlogged.

    Not a hide: the paper still appears, last. The negative it implies is already recorded
    properly in ``events``; this row only remembers where to put it.
    """

    __tablename__ = "account_dismissals"
    __table_args__ = (Index("ix_account_dismissals_recent", "user_sub", "dismissed_at"),)

    user_sub: Mapped[str] = mapped_column(
        ForeignKey("users.sub", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AccountFilterRow(Base):
    """The feed query string this account last applied, one row per account. Unlogged."""

    __tablename__ = "account_filters"

    user_sub: Mapped[str] = mapped_column(
        ForeignKey("users.sub", ondelete="CASCADE"), primary_key=True
    )
    query_string: Mapped[str] = mapped_column(String(2000))
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SignalRow(Base):
    __tablename__ = "signals"
    __table_args__ = (Index("ix_signals_paper_type_time", "paper_id", "type", "observed_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    value: Mapped[float] = mapped_column(Float)
    signal_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CitationEdgeRow(Base):
    __tablename__ = "citation_edges"
    __table_args__ = (Index("ix_citation_edges_cited", "cited_arxiv"),)

    citing_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    # The referenced work's normalized arXiv id: most references are not in the store yet, so
    # edges point at the external id and resolve through paper_external_ids at query time.
    cited_arxiv: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_paper_event", "paper_id", "event"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_sub: Mapped[str] = mapped_column(ForeignKey("users.sub", ondelete="CASCADE"))
    event: Mapped[str] = mapped_column(String)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    # Impression position in the surfaced list; position-bias correction needs it later.
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Event-specific magnitude (dwell milliseconds).
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Where it happened: feed, search, for-you, detail.
    surface: Mapped[str | None] = mapped_column(String, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CitationFetchRow(Base):
    __tablename__ = "citation_fetches"

    # Distinguishes "fetched, zero references" from "never fetched" — an empty edge set alone
    # cannot, and a transient fetch failure must not be cached as an empty result.
    citing_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PipelineLineageRow(Base):
    __tablename__ = "pipeline_lineage"
    __table_args__ = (
        Index("ix_pipeline_lineage_exited", "exited_at"),
        Index("ix_pipeline_lineage_stage_outcome", "stage", "outcome"),
        Index("ix_pipeline_lineage_stage_entered", "stage", "entered_at"),
    )

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    stage: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    # No FK: a packet can fail before its paper exists.
    paper_id: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Small per-stage facts (keyword method, candidate counts, chunk counts) for dashboards.
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class SchedulerRunRow(Base):
    """One completed scheduler task run — the ledger behind /v1/system/status."""

    __tablename__ = "scheduler_runs"
    __table_args__ = (
        Index("ix_scheduler_runs_finished", "finished_at"),
        Index("ix_scheduler_runs_task_finished", "task", "finished_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool] = mapped_column(Boolean)
    note: Mapped[str] = mapped_column(String(400), server_default="")


class AskMetricRow(Base):
    __tablename__ = "ask_metrics"
    __table_args__ = (Index("ix_ask_metrics_asked_at", "asked_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    mode: Mapped[str] = mapped_column(String)  # fast | llm
    surface: Mapped[str] = mapped_column(String)  # chat | ask
    question: Mapped[str] = mapped_column(String(200))
    retrieved: Mapped[int] = mapped_column(Integer)
    best_relevance: Mapped[float | None] = mapped_column(Float, nullable=True)
    found: Mapped[bool] = mapped_column(Boolean)
    retrieve_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rerank_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_ms: Mapped[int] = mapped_column(Integer)
