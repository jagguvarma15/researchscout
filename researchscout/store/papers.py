"""Idempotent persistence for canonical papers and their external-id map."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import cast, select, update
from sqlalchemy.dialects.postgresql import ARRAY, TEXT, insert
from sqlalchemy.orm import Session

from researchscout.schema import Author, Paper
from researchscout.store.models import ExternalIdRow, PaperRow


def upsert_paper(session: Session, paper: Paper) -> str:
    """Insert or update a paper by canonical id (never duplicates); returns the id.

    ``citation_count`` is deliberately absent: it is materialized from signals via
    :func:`set_citation_count`, and a content re-ingest must never reset it. Any future
    enriched-after-ingest column needs the same exclusion.
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
        set_={key: stmt.excluded[key] for key in values if key != "id"},
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


def list_papers(
    session: Session,
    *,
    days: int | None = None,
    categories: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Paper]:
    """List papers newest-first, optionally windowed by days and filtered by category."""
    stmt = select(PaperRow.id).order_by(PaperRow.published_at.desc())
    if days is not None:
        window_start = datetime.now(UTC) - timedelta(days=days)
        stmt = stmt.where(PaperRow.published_at >= window_start)
    if categories:
        stmt = stmt.where(PaperRow.categories.op("?|")(cast(categories, ARRAY(TEXT))))
    ids = session.execute(stmt.limit(limit).offset(offset)).scalars().all()
    papers = (get_paper(session, paper_id) for paper_id in ids)
    return [paper for paper in papers if paper is not None]


def get_paper(session: Session, paper_id: str) -> Paper | None:
    """Load a canonical paper (with its external ids) by id."""
    row = session.get(PaperRow, paper_id)
    if row is None:
        return None
    pairs = session.execute(
        select(ExternalIdRow.scheme, ExternalIdRow.value).where(ExternalIdRow.paper_id == paper_id)
    ).all()
    return Paper(
        id=row.id,
        external_ids={scheme: value for scheme, value in pairs},
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
