"""Facet filters for papers — one compiler shared by the feed and both retrieval legs.

``PaperFacets`` is the value object the API builds from query params; ``facets_where`` turns it
into a single SQL clause. Keeping the compiler here means the recency feed, the vector leg, and
the lexical leg can never drift apart on filter semantics.

Two axes describe a paper: ``subjects`` (the field it is in) and ``topics`` (the technique it
uses). Values within an axis are alternatives, so asking for two subjects widens the result;
the axes themselves narrow, so a subject and a topic together mean both. That is the reading a
filter panel implies, and the one a reader gets whether they arrive through the sidebar or by
editing the URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import ColumnClause, ColumnElement, and_, cast, false, func, literal_column, or_
from sqlalchemy.dialects.postgresql import ARRAY, TEXT

from researchscout.store.models import PaperRow
from researchscout.taxonomy import phrase_query, subject_for, topic_for

SortKey = Literal["newest", "citations", "activity"]

# The generated tsvector (migration 0007) and the archive expression index (migration 0020).
# Both are referenced as literal SQL because neither is a mapped column.
_SEARCH_TSV: ColumnClause[Any] = literal_column("papers.search_tsv")
_ARCHIVES = func.paper_archives(PaperRow.categories)


@dataclass(frozen=True)
class PaperFacets:
    days: int | None = None
    year: int | None = None
    month: int | None = None  # only meaningful with year
    categories: list[str] | None = None
    subjects: list[str] | None = None
    topics: list[str] | None = None
    author: str | None = None
    venue: str | None = None
    min_citations: int | None = None
    #: Paper ids to leave out entirely. This is what dismissing a paper does, and it is applied
    #: here rather than by dropping rows after the query so that ``total`` and the pager agree
    #: with what is actually on the page. Deliberately not applied under ``q``: a paper you
    #: dismissed from the feed should still be findable when you go looking for it.
    exclude: list[str] | None = None
    #: Restrict to exactly these paper ids ("ask about this paper"). Compiled like every
    #: other facet, so both retrieval legs and the chunk leg all honor the pin.
    only: list[str] | None = None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _overlaps_categories(codes: list[str]) -> ColumnElement[bool]:
    """Any of these category codes appears in the paper's list (GIN, cross-lists included)."""
    return PaperRow.categories.op("?|")(cast(sorted(codes), ARRAY(TEXT)))


def _overlaps_archives(archives: list[str]) -> ColumnElement[bool]:
    """The paper touches any of these archives (GIN over the paper_archives expression)."""
    return _ARCHIVES.op("&&")(cast(sorted(archives), ARRAY(TEXT)))


def _subjects_clause(keys: list[str]) -> ColumnElement[bool]:
    """Match any of the named subjects; an unknown key contributes nothing.

    A subject is a set of whole archives plus a set of individual codes, and each half has its
    own index, so the clause is a union of the two rather than one expression over both.
    """
    archives: set[str] = set()
    codes: set[str] = set()
    for key in keys:
        subject = subject_for(key)
        if subject is None:
            continue
        archives |= subject.archives
        codes |= subject.categories
    parts: list[ColumnElement[bool]] = []
    if archives:
        parts.append(_overlaps_archives(sorted(archives)))
    if codes:
        parts.append(_overlaps_categories(sorted(codes)))
    if not parts:
        return false()  # every key was unknown: match nothing rather than everything
    return or_(*parts)


def _topics_clause(keys: list[str]) -> ColumnElement[bool]:
    """Match any of the named topics, by category or by phrase depending on the topic."""
    codes: set[str] = set()
    phrases: list[str] = []
    for key in keys:
        topic = topic_for(key)
        if topic is None:
            continue
        codes |= topic.categories
        phrases.extend(topic.phrases)
    parts: list[ColumnElement[bool]] = []
    if codes:
        parts.append(_overlaps_categories(sorted(codes)))
    if phrases:
        # websearch_to_tsquery never raises on odd input, so a phrase list that yields no
        # lexemes simply matches nothing instead of failing the whole query.
        tsquery = func.websearch_to_tsquery("english", phrase_query(phrases))
        parts.append(_SEARCH_TSV.op("@@")(tsquery))
    if not parts:
        return false()
    return or_(*parts)


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
        clauses.append(_overlaps_categories(facets.categories))
    if facets.subjects:
        clauses.append(_subjects_clause(facets.subjects))
    if facets.topics:
        clauses.append(_topics_clause(facets.topics))
    if facets.author:
        pattern = f"%{_escape_like(facets.author)}%"
        clauses.append(literal_column("papers.author_names").ilike(pattern, escape="\\"))
    if facets.venue:
        pattern = f"%{_escape_like(facets.venue)}%"
        clauses.append(PaperRow.venue.ilike(pattern, escape="\\"))
    if facets.min_citations is not None:
        clauses.append(PaperRow.citation_count >= facets.min_citations)
    if facets.exclude:
        clauses.append(PaperRow.id.not_in(facets.exclude))
    if facets.only:
        clauses.append(PaperRow.id.in_(facets.only))
    if not clauses:
        return None
    return and_(*clauses)
