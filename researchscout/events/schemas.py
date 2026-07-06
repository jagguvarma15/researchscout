"""Versioned event payloads, JSON-serialized pydantic models keyed by topic."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from researchscout.schema import Paper

TOPIC_INGEST_JOBS = "ingest.jobs"
TOPIC_PAPERS_NEW = "papers.new"
TOPIC_PAPERS_SAVED = "papers.saved"
TOPIC_DIGESTS_PUBLISHED = "digests.published"


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


class PaperSaved(BaseModel):
    """A user saved (or unsaved) a paper; consumed by the Airtable reading-list sync."""

    schema_version: int = 1
    user_sub: str
    paper_id: str
    saved: bool
    at: datetime


class DigestPublished(BaseModel):
    """A weekly digest was (re)published; consumed by the Airtable archive sync."""

    schema_version: int = 1
    slug: str
    title: str
    period_start: datetime
    period_end: datetime
