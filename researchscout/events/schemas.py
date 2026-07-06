"""Versioned event payloads, JSON-serialized pydantic models keyed by topic."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from researchscout.schema import Paper

TOPIC_INGEST_JOBS = "ingest.jobs"
TOPIC_PAPERS_NEW = "papers.new"


class IngestJob(BaseModel):
    """A request to pull one source; produced by the scheduler, consumed by the ingest worker."""

    schema_version: int = 1
    source: str
    since: datetime
    max_items: int | None = None
    categories: list[str] | None = None


class PaperCreated(BaseModel):
    """A paper newly added to the store; consumed by the embed worker (and later syncs)."""

    schema_version: int = 1
    paper: Paper
