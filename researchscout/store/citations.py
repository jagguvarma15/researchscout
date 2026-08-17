"""Citation persistence: one-hop edges and the per-paper refresh watermark.

Edges point from a stored paper to the normalized arXiv id of a work it references — most
referenced papers are not in the store, so resolution to canonical ids happens at query time
through ``paper_external_ids``. A separate fetch record distinguishes "fetched, zero
references" from "never fetched", so transient lookup failures are never cached as empty.

The refresh watermark (``citation_refreshes``) is the citation walker's whole memory: papers
are refreshed stalest-first, so the watermark is the cursor and an interrupted walk resumes
tomorrow wherever coverage is thinnest, by construction.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from researchscout.store.models import (
    CitationEdgeRow,
    CitationFetchRow,
    CitationRefreshRow,
    ExternalIdRow,
    PaperRow,
)


def references_cached(session: Session, citing_id: str) -> list[str] | None:
    """The cached referenced arXiv ids, or None when this paper was never fetched."""
    if session.get(CitationFetchRow, citing_id) is None:
        return None
    rows = session.execute(
        select(CitationEdgeRow.cited_arxiv)
        .where(CitationEdgeRow.citing_id == citing_id)
        .order_by(CitationEdgeRow.cited_arxiv)
    ).scalars()
    return list(rows)


def store_references(session: Session, citing_id: str, cited_arxiv_ids: Sequence[str]) -> None:
    """Record one successful reference fetch and upsert its edges (idempotent)."""
    fetch = insert(CitationFetchRow).values(citing_id=citing_id, fetched_at=datetime.now(UTC))
    fetch = fetch.on_conflict_do_update(
        index_elements=["citing_id"], set_={"fetched_at": fetch.excluded.fetched_at}
    )
    session.execute(fetch)
    for cited in dict.fromkeys(cited_arxiv_ids):
        edge = insert(CitationEdgeRow).values(citing_id=citing_id, cited_arxiv=cited)
        session.execute(edge.on_conflict_do_nothing(index_elements=["citing_id", "cited_arxiv"]))
    session.flush()


def stalest_citation_targets(session: Session, *, limit: int) -> list[tuple[str, str]]:
    """(paper_id, arxiv_id) pairs in refresh order: never-fetched first (newest published
    leading), then oldest coverage first.

    This ordering is the walker's cursor: whatever a partial run stamps moves to the back of
    the queue, so the next run naturally continues where coverage is thinnest.
    """
    rows = session.execute(
        select(ExternalIdRow.paper_id, ExternalIdRow.value)
        .join(PaperRow, PaperRow.id == ExternalIdRow.paper_id)
        .join(CitationRefreshRow, CitationRefreshRow.paper_id == PaperRow.id, isouter=True)
        .where(ExternalIdRow.scheme == "arxiv")
        .order_by(
            CitationRefreshRow.fetched_at.asc().nulls_first(),
            PaperRow.published_at.desc(),
            ExternalIdRow.paper_id,
        )
        .limit(limit)
    ).all()
    return [(paper_id, arxiv_id) for paper_id, arxiv_id in rows]


def stale_fallback_targets(
    session: Session, *, older_than: datetime, limit: int
) -> list[tuple[str, str]]:
    """Papers the primary source has not covered lately: watermark absent or older than
    ``older_than``. Evaluated after the primary pass, so anything it just stamped is out.
    """
    rows = session.execute(
        select(ExternalIdRow.paper_id, ExternalIdRow.value)
        .join(PaperRow, PaperRow.id == ExternalIdRow.paper_id)
        .join(CitationRefreshRow, CitationRefreshRow.paper_id == PaperRow.id, isouter=True)
        .where(
            ExternalIdRow.scheme == "arxiv",
            (CitationRefreshRow.fetched_at.is_(None))
            | (CitationRefreshRow.fetched_at < older_than),
        )
        .order_by(
            CitationRefreshRow.fetched_at.asc().nulls_first(),
            PaperRow.published_at.desc(),
            ExternalIdRow.paper_id,
        )
        .limit(limit)
    ).all()
    return [(paper_id, arxiv_id) for paper_id, arxiv_id in rows]


def mark_citations_refreshed(
    session: Session, paper_ids: Iterable[str], *, source: str, fetched_at: datetime
) -> None:
    """Stamp papers as citation-refreshed by ``source`` (idempotent upsert)."""
    values = [
        {"paper_id": paper_id, "source": source, "fetched_at": fetched_at}
        for paper_id in dict.fromkeys(paper_ids)
    ]
    if not values:
        return
    stmt = insert(CitationRefreshRow).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["paper_id"],
        set_={"source": stmt.excluded.source, "fetched_at": stmt.excluded.fetched_at},
    )
    session.execute(stmt)


def citing_ids_for(session: Session, cited_arxiv: str) -> list[str]:
    """Stored papers whose fetched references include this arXiv id (for co-citation later)."""
    rows = session.execute(
        select(CitationEdgeRow.citing_id)
        .where(CitationEdgeRow.cited_arxiv == cited_arxiv)
        .order_by(CitationEdgeRow.citing_id)
    ).scalars()
    return list(rows)
