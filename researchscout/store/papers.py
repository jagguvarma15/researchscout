"""Idempotent persistence for canonical papers and their external-id map."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from researchscout.config import get_settings
from researchscout.schema import Author, Paper
from researchscout.store.facets import PaperFacets, SortKey, facets_where
from researchscout.store.models import ExternalIdRow, PaperRow, SignalRow

# Columns written after ingest (scout fulltext, the streaming inject stage). A content
# re-ingest carries None for these, so the conflict update must never touch them --
# the same contract that keeps citation_count out of the values dict entirely.
_ENRICHED_AFTER_INGEST = frozenset({"full_text"})


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
    }
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
    stmt = select(PaperRow)
    where = facets_where(facets)
    if where is not None:
        stmt = stmt.where(where)
    rows = session.execute(_apply_sort(stmt, sort).limit(limit).offset(offset)).scalars().all()
    return _rows_to_papers(session, rows)


def count_papers(session: Session, facets: PaperFacets) -> int:
    """How many papers match the facets (no pagination)."""
    stmt = select(func.count()).select_from(PaperRow)
    where = facets_where(facets)
    if where is not None:
        stmt = stmt.where(where)
    return session.execute(stmt).scalar_one()


def set_full_text(session: Session, paper_id: str, text: str) -> None:
    """Store extracted full text; an empty string marks "checked, no HTML available"."""
    session.execute(update(PaperRow).where(PaperRow.id == paper_id).values(full_text=text))


def papers_missing_full_text(
    session: Session, *, limit: int, first: Sequence[str] = ()
) -> list[tuple[str, str]]:
    """(paper_id, arXiv id) for papers never checked for full text, ``first`` ids leading.

    Papers marked with an empty string (checked, unavailable) are excluded, so the batch never
    re-fetches the PDF-only tail.
    """
    stmt = (
        select(PaperRow.id, ExternalIdRow.value)
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
    return [(paper_id, arxiv_id) for paper_id, arxiv_id in rows]


def get_papers(session: Session, paper_ids: Sequence[str]) -> dict[str, Paper]:
    """Load many canonical papers by id in two queries; unknown ids are simply absent."""
    if not paper_ids:
        return {}
    rows = session.execute(select(PaperRow).where(PaperRow.id.in_(list(paper_ids)))).scalars().all()
    return {paper.id: paper for paper in _rows_to_papers(session, rows)}


def get_paper(session: Session, paper_id: str) -> Paper | None:
    """Load a canonical paper (with its external ids) by id."""
    row = session.get(PaperRow, paper_id)
    if row is None:
        return None
    pairs = session.execute(
        select(ExternalIdRow.scheme, ExternalIdRow.value).where(ExternalIdRow.paper_id == paper_id)
    ).all()
    return _row_to_paper(row, {scheme: value for scheme, value in pairs})


def _rows_to_papers(session: Session, rows: Sequence[PaperRow]) -> list[Paper]:
    """Convert rows in order, loading all external ids in one query (no per-row round trips)."""
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
    return [_row_to_paper(row, external.get(row.id, {})) for row in rows]


def _row_to_paper(row: PaperRow, external_ids: dict[str, str]) -> Paper:
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
        full_text=row.full_text,
        citation_count=row.citation_count,
    )
