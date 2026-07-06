"""ORM models: papers, external-id map, raw landing, ingest cursors, embeddings, and signals."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from researchscout.store.db import Base


class PaperRow(Base):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str] = mapped_column(Text)
    authors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    categories: Mapped[list[str]] = mapped_column(JSONB, default=list)
    venue: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_text: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class DigestRow(Base):
    __tablename__ = "digests"

    slug: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    body: Mapped[str] = mapped_column(Text)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SavedPaperRow(Base):
    __tablename__ = "saved_papers"

    user_sub: Mapped[str] = mapped_column(String, primary_key=True)
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
