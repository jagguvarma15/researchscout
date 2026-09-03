"""Persistence for digests (weekly issues and daily reports; re-runs replace their slug)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from researchscout.digest import Digest, RankedPaper
from researchscout.store.models import DigestRow

_ITEM_KEYWORDS = 4
_ITEM_AUTHORS = 3


def _item_payload(item: RankedPaper) -> dict[str, Any]:
    """One stored item: the ranked facts plus the enrichment already loaded on the paper.

    Optional keys are omitted when empty rather than bound to None - a bound None lands as
    JSON 'null', not SQL NULL, and readers treat present-but-null as data.
    """
    paper = item.paper
    payload: dict[str, Any] = {
        "paper_id": paper.id,
        "title": paper.title,
        "score": item.score,
        "citations": item.citations,
    }
    if paper.primary_category:
        payload["primary_category"] = paper.primary_category
    if paper.keywords:
        payload["keywords"] = paper.keywords[:_ITEM_KEYWORDS]
    if paper.authors:
        payload["authors"] = [author.name for author in paper.authors[:_ITEM_AUTHORS]]
        payload["author_count"] = len(paper.authors)
    if paper.venue:
        payload["venue"] = paper.venue
    if item.contributions:
        payload["why"] = {key: round(value, 3) for key, value in item.contributions.items()}
    return payload


def upsert_digest(session: Session, digest: Digest) -> None:
    """Insert or replace the digest for its slug."""
    values = {
        "slug": digest.slug,
        "kind": digest.kind,
        "title": digest.title,
        "period_start": digest.period_start,
        "period_end": digest.period_end,
        "body": digest.body,
        "llm_ok": digest.llm_ok,
        "items": [_item_payload(item) for item in digest.items],
    }
    stmt = insert(DigestRow).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["slug"],
        set_={key: stmt.excluded[key] for key in values if key != "slug"},
    )
    session.execute(stmt)


def get_digest(session: Session, slug: str) -> DigestRow | None:
    return session.get(DigestRow, slug)


def list_digests(
    session: Session, *, kind: str | None = None, limit: int = 20, offset: int = 0
) -> list[DigestRow]:
    """Digests newest first, optionally one kind only."""
    stmt = select(DigestRow).order_by(DigestRow.period_end.desc())
    if kind is not None:
        stmt = stmt.where(DigestRow.kind == kind)
    rows = session.execute(stmt.limit(limit).offset(offset)).scalars()
    return list(rows)


def count_digests(session: Session, *, kind: str | None = None) -> int:
    """How many digests exist under the kind filter (no pagination)."""
    stmt = select(func.count()).select_from(DigestRow)
    if kind is not None:
        stmt = stmt.where(DigestRow.kind == kind)
    return int(session.execute(stmt).scalar_one())
