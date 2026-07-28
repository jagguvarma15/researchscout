"""Persisted one-hop citation edges (cache-first over the live Semantic Scholar lookups).

Edges point from a stored paper to the normalized arXiv id of a work it references — most
referenced papers are not in the store, so resolution to canonical ids happens at query time
through ``paper_external_ids``. A separate fetch record distinguishes "fetched, zero
references" from "never fetched", so transient lookup failures are never cached as empty.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from researchscout.store.models import CitationEdgeRow, CitationFetchRow


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


def citing_ids_for(session: Session, cited_arxiv: str) -> list[str]:
    """Stored papers whose fetched references include this arXiv id (for co-citation later)."""
    rows = session.execute(
        select(CitationEdgeRow.citing_id)
        .where(CitationEdgeRow.cited_arxiv == cited_arxiv)
        .order_by(CitationEdgeRow.citing_id)
    ).scalars()
    return list(rows)
