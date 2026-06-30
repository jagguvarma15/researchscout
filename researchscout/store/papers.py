"""Idempotent persistence for canonical papers and their external-id map."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from researchscout.schema import Author, Paper
from researchscout.store.models import ExternalIdRow, PaperRow


def upsert_paper(session: Session, paper: Paper) -> str:
    """Insert or update a paper by canonical id (never duplicates); returns the id."""
    values = {
        "id": paper.id,
        "title": paper.title,
        "abstract": paper.abstract,
        "authors": [author.model_dump() for author in paper.authors],
        "categories": list(paper.categories),
        "venue": paper.venue,
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
        venue=row.venue,
        published_at=row.published_at,
        updated_at=row.updated_at,
        source=row.source,
        url=row.url,
        pdf_url=row.pdf_url,
        full_text=row.full_text,
    )
