"""Canonical data schema for ResearchScout.

Every source normalizes into one of two shapes — :class:`Paper` (content) or :class:`Signal`
(evidence about a paper over time). :func:`canonical_id` defines the cross-source identity used
to collapse the same paper seen from different sources into one record.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Author(BaseModel):
    name: str
    affiliation: str | None = None


class SignalType(StrEnum):
    """Kinds of evidence about a paper's importance, observed over time."""

    citation = "citation"
    social_mention = "social_mention"
    code_stars = "code_stars"
    hf_trending_rank = "hf_trending_rank"
    review_score = "review_score"
    discussion = "discussion"


class Paper(BaseModel):
    """A research paper or document produced by a content source."""

    id: str
    external_ids: dict[str, str] = Field(default_factory=dict)
    title: str
    abstract: str
    authors: list[Author] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    primary_category: str | None = None
    venue: str | None = None
    comment: str | None = None
    published_at: datetime
    updated_at: datetime | None = None
    source: str
    url: str | None = None
    pdf_url: str | None = None
    full_text: str | None = None
    # Materialized from citation signals by the store; sources never set it.
    citation_count: int = 0


class Signal(BaseModel):
    """A timestamped observation about a paper. Stored append-only (see Stage 2)."""

    paper_id: str
    type: SignalType
    source: str
    value: float
    metadata: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime


_ARXIV_PREFIX_RE = re.compile(r"^arxiv:", re.IGNORECASE)
_ARXIV_VERSION_RE = re.compile(r"v\d+$")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_arxiv_id(raw: str) -> str:
    """Return a bare arXiv id: strip an ``arXiv:`` prefix and a trailing ``vN`` version.

    ``"arXiv:2401.12345v2"`` -> ``"2401.12345"``. The version is intentionally dropped from the
    identity; connectors keep it in ``external_ids``/metadata when they need it.
    """
    bare = _ARXIV_PREFIX_RE.sub("", raw.strip())
    return _ARXIV_VERSION_RE.sub("", bare)


def _normalize_title(title: str) -> str:
    folded = unicodedata.normalize("NFKD", title).lower()
    return " ".join(_NON_ALNUM_RE.sub(" ", folded).split())


def _first_author_surname(authors: list[Author]) -> str:
    if not authors:
        return ""
    parts = authors[0].name.split()
    return parts[-1].lower() if parts else ""


def canonical_id(
    external_ids: dict[str, str],
    title: str,
    authors: list[Author],
) -> str:
    """Compute the canonical paper id used for cross-source dedup.

    Precedence: ``arxiv:`` > ``doi:`` > ``hash:`` of the normalized title plus the first author's
    surname. The same paper described by different sources must yield the same id — this is the
    contract the ingest pipeline (PR 04) relies on to collapse duplicates.
    """
    arxiv = external_ids.get("arxiv")
    if arxiv:
        return f"arxiv:{normalize_arxiv_id(arxiv)}"
    doi = external_ids.get("doi")
    if doi:
        return f"doi:{doi.strip().lower()}"
    basis = f"{_normalize_title(title)}|{_first_author_surname(authors)}"
    digest = hashlib.sha1(basis.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"hash:{digest}"
