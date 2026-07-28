"""Facet filters for papers — one compiler shared by the feed and both retrieval legs.

``PaperFacets`` is the value object the API builds from query params; ``facets_where`` turns it
into a single SQL clause. Keeping the compiler here means the recency feed, the vector leg, and
the lexical leg can never drift apart on filter semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import ColumnElement, and_, cast, false, func, literal_column
from sqlalchemy.dialects.postgresql import ARRAY, TEXT

from researchscout.store.models import PaperRow
from researchscout.taxonomy import AI_CATEGORIES, archives_for, archives_for_group

SortKey = Literal["newest", "citations", "activity"]


@dataclass(frozen=True)
class PaperFacets:
    days: int | None = None
    year: int | None = None
    month: int | None = None  # only meaningful with year
    categories: list[str] | None = None
    kind: Literal["tech", "non_tech", "ai"] | None = None
    groups: list[str] | None = None
    author: str | None = None
    venue: str | None = None
    min_citations: int | None = None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _archives(facets: PaperFacets) -> frozenset[str] | None:
    """The archive prefixes implied by kind/groups; None when neither narrows by archive.

    kind=ai filters by category overlap in ``facets_where`` instead, so groups still AND
    independently: ``kind=ai&group=cs`` means AI-overlapping papers with a cs primary archive.
    """
    kind_set: frozenset[str] | None = None
    if facets.kind == "tech" or facets.kind == "non_tech":
        kind_set = archives_for(facets.kind)
    group_set: frozenset[str] | None = None
    if facets.groups:
        group_set = frozenset().union(*(archives_for_group(g) for g in facets.groups))
    if kind_set is None:
        return group_set
    if group_set is None:
        return kind_set
    return kind_set & group_set


def facets_where(facets: PaperFacets) -> ColumnElement[bool] | None:
    """Compile the facets into one WHERE clause, or None when nothing filters."""
    clauses: list[ColumnElement[bool]] = []
    if facets.days is not None:
        window_start = datetime.now(UTC) - timedelta(days=facets.days)
        clauses.append(PaperRow.published_at >= window_start)
    if facets.year is not None:
        start = datetime(facets.year, facets.month or 1, 1, tzinfo=UTC)
        if facets.month:
            next_year = facets.year + (1 if facets.month == 12 else 0)
            end = datetime(next_year, facets.month % 12 + 1, 1, tzinfo=UTC)
        else:
            end = datetime(facets.year + 1, 1, 1, tzinfo=UTC)
        clauses.append(PaperRow.published_at >= start)
        clauses.append(PaperRow.published_at < end)
    if facets.categories:
        clauses.append(PaperRow.categories.op("?|")(cast(facets.categories, ARRAY(TEXT))))
    if facets.kind == "ai":
        # Same GIN-indexed overlap path as the categories facet, so cross-lists count.
        clauses.append(PaperRow.categories.op("?|")(cast(sorted(AI_CATEGORIES), ARRAY(TEXT))))
    archives = _archives(facets)
    if archives is not None:
        if archives:
            # Matches the expression index on split_part(primary_category, '.', 1).
            clauses.append(func.split_part(PaperRow.primary_category, ".", 1).in_(sorted(archives)))
        else:
            clauses.append(false())  # kind and groups intersect to nothing
    if facets.author:
        pattern = f"%{_escape_like(facets.author)}%"
        clauses.append(literal_column("papers.author_names").ilike(pattern, escape="\\"))
    if facets.venue:
        pattern = f"%{_escape_like(facets.venue)}%"
        clauses.append(PaperRow.venue.ilike(pattern, escape="\\"))
    if facets.min_citations is not None:
        clauses.append(PaperRow.citation_count >= facets.min_citations)
    if not clauses:
        return None
    return and_(*clauses)
