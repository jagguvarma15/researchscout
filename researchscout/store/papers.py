"""Idempotent persistence for canonical papers and their external-id map.

Listing and loading are deliberately different reads. A paper row carries its full article
text, which averages 18 kB and is what the chunker and the answer path work from -- and which
nothing in a list of papers ever shows. Selecting it anyway cost 469 kB per feed page, over 90%
of it thrown away by ``PaperSummary`` before the response was built, on every feed render and
every keystroke in the omnibox. So ``list_papers`` defers that column and the detail reads keep
it. The two are one line apart and easy to conflate, which is why they say so here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, defer

from researchscout.config import get_settings
from researchscout.schema import Author, Paper, PaperLabel
from researchscout.store.facets import PaperFacets, SortKey, facets_where
from researchscout.store.models import ExternalIdRow, PaperRow, SignalRow

# Columns written after ingest (scout fulltext, the streaming stages). A content
# re-ingest carries None for these, so the conflict update must never touch them --
# the same contract that keeps citation_count out of the values dict entirely.
_ENRICHED_AFTER_INGEST = frozenset({"full_text", "keywords", "sections", "labels"})


def upsert_paper(session: Session, paper: Paper) -> str:
    """Insert or update a paper by canonical id (never duplicates); returns the id.

    ``citation_count`` is deliberately absent: it is materialized from signals via
    :func:`set_citation_count`, and a content re-ingest must never reset it. Columns in
    ``_ENRICHED_AFTER_INGEST`` are inserted but excluded from the conflict update for
    the same reason.
    """
    values = {
        "id": paper.id,
        "title": paper.title,
        "abstract": paper.abstract,
        "authors": [author.model_dump() for author in paper.authors],
        "categories": list(paper.categories),
        "primary_category": paper.primary_category,
        "venue": paper.venue,
        "comment": paper.comment,
        "published_at": paper.published_at,
        "updated_at": paper.updated_at,
        "source": paper.source,
        "url": paper.url,
        "pdf_url": paper.pdf_url,
        "full_text": paper.full_text,
        "keywords": paper.keywords,
        "sections": paper.sections,
        "labels": (
            [label.model_dump() for label in paper.labels] if paper.labels is not None else None
        ),
    }
    # The JSON columns must land as SQL NULL, not JSON null: every "still unprocessed"
    # query says IS NULL, and SQLAlchemy's JSON type serializes a bound None as the JSON
    # 'null' value instead. Omitting the keys lets the column default apply.
    for key in ("keywords", "sections", "labels"):
        if values[key] is None:
            del values[key]
    stmt = insert(PaperRow).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_={
            key: stmt.excluded[key]
            for key in values
            if key != "id" and key not in _ENRICHED_AFTER_INGEST
        },
    )
    session.execute(stmt)
    link_external_ids(session, paper.id, paper.external_ids)
    return paper.id


def set_citation_count(session: Session, paper_id: str, count: int) -> None:
    """Materialize the latest citation count onto the paper row (signals stay source of truth)."""
    session.execute(update(PaperRow).where(PaperRow.id == paper_id).values(citation_count=count))


def link_external_ids(session: Session, paper_id: str, external_ids: dict[str, str]) -> None:
    """Point each (scheme, value) at the canonical paper id (idempotent)."""
    for scheme, value in external_ids.items():
        stmt = insert(ExternalIdRow).values(scheme=scheme, value=value, paper_id=paper_id)
        stmt = stmt.on_conflict_do_update(
            index_elements=["scheme", "value"], set_={"paper_id": paper_id}
        )
        session.execute(stmt)


def find_by_external_id(session: Session, scheme: str, value: str) -> str | None:
    """Resolve an external id to a canonical paper id, if known."""
    return session.execute(
        select(ExternalIdRow.paper_id).where(
            ExternalIdRow.scheme == scheme,
            ExternalIdRow.value == value,
        )
    ).scalar_one_or_none()


def enrichment_watermarks(
    session: Session, *, published_after: datetime
) -> dict[tuple[str, str], tuple[datetime | None, bool]]:
    """Map ``(scheme, value)`` to ``(updated_at, enriched)`` for papers after the cutoff.

    The stream producers consult this to skip re-publishing papers the pipeline has
    already enriched (enriched = the categorize stage wrote keywords). Batch-ingested
    papers report False, so the stream still enriches them exactly once.
    """
    rows = session.execute(
        select(
            ExternalIdRow.scheme,
            ExternalIdRow.value,
            PaperRow.updated_at,
            PaperRow.keywords.is_not(None),
        )
        .join(PaperRow, PaperRow.id == ExternalIdRow.paper_id)
        .where(PaperRow.published_at >= published_after)
    ).all()
    return {(scheme, value): (updated, enriched) for scheme, value, updated, enriched in rows}


def _apply_sort(stmt: Select[tuple[PaperRow]], sort: SortKey) -> Select[tuple[PaperRow]]:
    if sort == "citations":
        return stmt.order_by(PaperRow.citation_count.desc(), PaperRow.published_at.desc())
    if sort == "activity":
        window_start = datetime.now(UTC) - timedelta(days=get_settings().freshness_days)
        activity = (
            select(SignalRow.paper_id, func.count().label("n"))
            .where(SignalRow.observed_at >= window_start)
            .group_by(SignalRow.paper_id)
            .subquery()
        )
        return stmt.outerjoin(activity, activity.c.paper_id == PaperRow.id).order_by(
            func.coalesce(activity.c.n, 0).desc(), PaperRow.published_at.desc()
        )
    return stmt.order_by(PaperRow.published_at.desc(), PaperRow.id)


def list_papers(
    session: Session,
    *,
    facets: PaperFacets | None = None,
    sort: SortKey = "newest",
    days: int | None = None,
    categories: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Paper]:
    """List papers ordered by ``sort`` and filtered by ``facets``.

    The legacy ``days``/``categories`` kwargs fold into a facets object when none is given, so
    existing callers keep their meaning.
    """
    if facets is None:
        facets = PaperFacets(days=days, categories=categories)
    # raiseload=False so a caller that does touch full_text on a listed paper gets a lazy load
    # rather than an exception; nothing in the API does, and the point is the bytes not moved.
    stmt = select(PaperRow).options(defer(PaperRow.full_text, raiseload=False))
    where = facets_where(facets)
    if where is not None:
        stmt = stmt.where(where)
    rows = session.execute(_apply_sort(stmt, sort).limit(limit).offset(offset)).scalars().all()
    return _rows_to_papers(session, rows, full_text=False)


def papers_arrived_since(session: Session, since: datetime, *, limit: int = 500) -> list[Paper]:
    """Papers first stored at or after ``since``, newest arrivals first.

    Arrival (``created_at``) rather than publication: arXiv's published_at is submission time,
    which trails the announcement that actually delivers a paper here by a day or more. "What
    arrived today" is a question about this corpus, not about the calendar, and it is the
    daily report's window - filtering the report by published_at left it empty on almost
    every real day.
    """
    stmt = (
        select(PaperRow)
        .options(defer(PaperRow.full_text, raiseload=False))
        .where(PaperRow.created_at >= since)
        .order_by(PaperRow.created_at.desc(), PaperRow.published_at.desc(), PaperRow.id)
        .limit(limit)
    )
    rows = session.execute(stmt).scalars().all()
    return _rows_to_papers(session, rows, full_text=False)


def count_papers(session: Session, facets: PaperFacets) -> int:
    """How many papers match the facets (no pagination)."""
    stmt = select(func.count()).select_from(PaperRow)
    where = facets_where(facets)
    if where is not None:
        stmt = stmt.where(where)
    return session.execute(stmt).scalar_one()


# A fetch that finds no article text is only conclusive once the paper is old enough:
# arXiv renders HTML hours-to-days after the announcement, and the nightly batch runs the
# same night. Inside the grace window a miss stays NULL and the next batch retries it.
_FULLTEXT_TOMBSTONE_GRACE = timedelta(days=7)


def set_full_text(session: Session, paper_id: str, text: str) -> None:
    """Store extracted full text; an empty string marks "checked, no HTML available"."""
    session.execute(update(PaperRow).where(PaperRow.id == paper_id).values(full_text=text))


def record_full_text_result(
    session: Session,
    paper_id: str,
    text: str | None,
    *,
    published_at: datetime,
    now: datetime | None = None,
) -> None:
    """Store a fetch outcome with grace: real text always; the tombstone only when the
    paper is old enough that "no HTML" means unavailable rather than not rendered yet.

    A transient failure on a fresh paper leaves the row NULL, so it stays in the pending
    queue instead of being permanently marked as checked.
    """
    if text:
        set_full_text(session, paper_id, text)
        return
    now = now or datetime.now(UTC)
    if now - published_at > _FULLTEXT_TOMBSTONE_GRACE:
        set_full_text(session, paper_id, "")


def set_enrichment(
    session: Session,
    paper_id: str,
    *,
    keywords: list[str] | None = None,
    sections: list[str] | None = None,
    labels: list[PaperLabel] | None = None,
) -> None:
    """Write categorize-stage output onto the paper row; a None argument leaves its column alone."""
    values: dict[str, object] = {}
    if keywords is not None:
        values["keywords"] = keywords
    if sections is not None:
        values["sections"] = sections
    if labels is not None:
        values["labels"] = [label.model_dump() for label in labels]
    if values:
        session.execute(update(PaperRow).where(PaperRow.id == paper_id).values(**values))


def papers_missing_full_text(
    session: Session, *, limit: int, first: Sequence[str] = ()
) -> list[tuple[str, str, datetime]]:
    """(paper_id, arXiv id, published_at) for papers never checked for full text,
    ``first`` ids leading.

    Papers marked with an empty string (checked, unavailable) are excluded, so the batch never
    re-fetches the PDF-only tail. ``published_at`` rides along so the caller can apply the
    tombstone grace without a second read.
    """
    stmt = (
        select(PaperRow.id, ExternalIdRow.value, PaperRow.published_at)
        .join(
            ExternalIdRow,
            (ExternalIdRow.paper_id == PaperRow.id) & (ExternalIdRow.scheme == "arxiv"),
        )
        .where(PaperRow.full_text.is_(None))
    )
    if first:
        stmt = stmt.order_by(PaperRow.id.in_(list(first)).desc(), PaperRow.published_at.desc())
    else:
        stmt = stmt.order_by(PaperRow.published_at.desc())
    rows = session.execute(stmt.limit(limit)).all()
    return [(paper_id, arxiv_id, published_at) for paper_id, arxiv_id, published_at in rows]


def papers_missing_keywords(
    session: Session, *, limit: int
) -> list[tuple[str, str, str, str | None]]:
    """(id, title, abstract, primary_category) for papers never categorized, newest first.

    A written keyword list - even an empty one - marks the paper as processed, the same
    checked-marker convention the full-text tombstone uses.
    """
    stmt = (
        select(PaperRow.id, PaperRow.title, PaperRow.abstract, PaperRow.primary_category)
        .where(PaperRow.keywords.is_(None))
        .order_by(PaperRow.created_at.desc())
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    return [(paper_id, title, abstract, primary) for paper_id, title, abstract, primary in rows]


def get_papers(session: Session, paper_ids: Sequence[str]) -> dict[str, Paper]:
    """Load many canonical papers by id in two queries; unknown ids are simply absent.

    This is retrieval's hydration step, so it is on the latency path of every answer and every
    search. Full text is left behind for the same reason as the feed: the callers rank, cite
    and summarize from titles, abstracts and chunks, never from this column.
    """
    if not paper_ids:
        return {}
    rows = (
        session.execute(
            select(PaperRow)
            .options(defer(PaperRow.full_text, raiseload=False))
            .where(PaperRow.id.in_(list(paper_ids)))
        )
        .scalars()
        .all()
    )
    return {paper.id: paper for paper in _rows_to_papers(session, rows, full_text=False)}


def get_paper(session: Session, paper_id: str) -> Paper | None:
    """Load a canonical paper (with its external ids) by id."""
    row = session.get(PaperRow, paper_id)
    if row is None:
        return None
    pairs = session.execute(
        select(ExternalIdRow.scheme, ExternalIdRow.value).where(ExternalIdRow.paper_id == paper_id)
    ).all()
    return _row_to_paper(row, {scheme: value for scheme, value in pairs})


def _rows_to_papers(
    session: Session, rows: Sequence[PaperRow], *, full_text: bool = True
) -> list[Paper]:
    """Convert rows in order, loading all external ids in one query (no per-row round trips).

    ``full_text=False`` goes with a deferred column: reading the attribute would otherwise
    lazy-load it one row at a time, which is slower than never having deferred it.
    """
    ids = [row.id for row in rows]
    external: dict[str, dict[str, str]] = {}
    if ids:
        pairs = session.execute(
            select(ExternalIdRow.paper_id, ExternalIdRow.scheme, ExternalIdRow.value).where(
                ExternalIdRow.paper_id.in_(ids)
            )
        ).all()
        for paper_id, scheme, value in pairs:
            external.setdefault(paper_id, {})[scheme] = value
    return [_row_to_paper(row, external.get(row.id, {}), full_text=full_text) for row in rows]


def _row_to_paper(row: PaperRow, external_ids: dict[str, str], *, full_text: bool = True) -> Paper:
    return Paper(
        id=row.id,
        external_ids=external_ids,
        title=row.title,
        abstract=row.abstract,
        authors=[Author(**author) for author in row.authors],
        categories=list(row.categories),
        primary_category=row.primary_category,
        venue=row.venue,
        comment=row.comment,
        published_at=row.published_at,
        updated_at=row.updated_at,
        source=row.source,
        url=row.url,
        pdf_url=row.pdf_url,
        full_text=row.full_text if full_text else None,
        keywords=row.keywords,
        sections=row.sections,
        labels=([PaperLabel(**label) for label in row.labels] if row.labels is not None else None),
        citation_count=row.citation_count,
    )
