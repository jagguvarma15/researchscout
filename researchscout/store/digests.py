"""Persistence for weekly digests (one row per ISO week; re-runs replace the week)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from researchscout.digest import Digest
from researchscout.store.models import DigestRow


def upsert_digest(session: Session, digest: Digest) -> None:
    """Insert or replace the digest for its week."""
    values = {
        "slug": digest.slug,
        "title": digest.title,
        "period_start": digest.period_start,
        "period_end": digest.period_end,
        "body": digest.body,
        "items": [
            {
                "paper_id": item.paper.id,
                "title": item.paper.title,
                "score": item.score,
                "citations": item.citations,
            }
            for item in digest.items
        ],
    }
    stmt = insert(DigestRow).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["slug"],
        set_={key: stmt.excluded[key] for key in values if key != "slug"},
    )
    session.execute(stmt)


def get_digest(session: Session, slug: str) -> DigestRow | None:
    return session.get(DigestRow, slug)


def list_digests(session: Session, *, limit: int = 20) -> list[DigestRow]:
    """Digests newest week first."""
    rows = session.execute(
        select(DigestRow).order_by(DigestRow.period_end.desc()).limit(limit)
    ).scalars()
    return list(rows)
