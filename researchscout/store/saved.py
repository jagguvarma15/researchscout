"""Per-user reading list. Identity is the caller's ``sub`` claim — no local user table.

Since migration 0030 a save is a library row: reading status, tags, and a note ride the
same (user_sub, paper_id) key. Updates go through ``update_saved`` with an explicit
change dict so "not provided" and "clear this" stay different things, and JSONB values
clear to SQL NULL rather than the JSON-null imposter 0028 had to heal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, null, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from researchscout.schema import Paper
from researchscout.store.models import PaperEmbeddingRow, PaperRow, SavedPaperRow
from researchscout.store.papers import get_papers

SAVED_STATUSES = ("to-read", "reading", "done")


@dataclass(frozen=True)
class SavedEntry:
    """One reading-list row with its library fields, ready for the API."""

    paper: Paper
    status: str
    tags: list[str]
    note: str | None
    saved_at: datetime


def save_paper(session: Session, user_sub: str, paper_id: str) -> bool:
    """Save a paper for a user; returns False when it was already saved (idempotent)."""
    stmt = (
        insert(SavedPaperRow)
        .values(user_sub=user_sub, paper_id=paper_id)
        .on_conflict_do_nothing(index_elements=["user_sub", "paper_id"])
        .returning(SavedPaperRow.paper_id)
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def unsave_paper(session: Session, user_sub: str, paper_id: str) -> bool:
    """Remove a saved paper; returns False when it was not saved (idempotent)."""
    stmt = (
        delete(SavedPaperRow)
        .where(SavedPaperRow.user_sub == user_sub, SavedPaperRow.paper_id == paper_id)
        .returning(SavedPaperRow.paper_id)
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def update_saved(session: Session, user_sub: str, paper_id: str, changes: dict[str, Any]) -> bool:
    """Apply a PATCH's provided fields to one saved row; False when nothing is saved.

    ``changes`` holds exactly the fields the caller provided - an absent key changes
    nothing, an empty tags list or None note clears to SQL NULL.
    """
    allowed = {"status", "tags", "note"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"unknown saved fields: {sorted(unknown)}")
    if not changes:
        return session.get(SavedPaperRow, (user_sub, paper_id)) is not None
    values: dict[str, Any] = {}
    for field, value in changes.items():
        values[field] = null() if value in (None, []) else value
    result = session.execute(
        update(SavedPaperRow)
        .where(SavedPaperRow.user_sub == user_sub, SavedPaperRow.paper_id == paper_id)
        .values(**values)
        .returning(SavedPaperRow.paper_id)
    )
    return result.scalar_one_or_none() is not None


def list_saved(
    session: Session,
    user_sub: str,
    *,
    status: str | None = None,
    tag: str | None = None,
    query: str | None = None,
    sort: str = "saved",
) -> list[SavedEntry]:
    """A user's reading list with its library fields, filtered and ordered.

    ``sort`` is saved (recency of the save, the default), published, or title. The text
    filter is a plain substring over titles - a library is tens of rows, not a corpus.
    """
    stmt = (
        select(SavedPaperRow, PaperRow.title, PaperRow.published_at)
        .join(PaperRow, PaperRow.id == SavedPaperRow.paper_id)
        .where(SavedPaperRow.user_sub == user_sub)
    )
    if status:
        stmt = stmt.where(SavedPaperRow.status == status)
    if tag:
        stmt = stmt.where(SavedPaperRow.tags.op("?")(tag))
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(PaperRow.title.ilike(f"%{escaped}%", escape="\\"))
    if sort == "published":
        stmt = stmt.order_by(PaperRow.published_at.desc())
    elif sort == "title":
        stmt = stmt.order_by(PaperRow.title.asc())
    else:
        stmt = stmt.order_by(SavedPaperRow.saved_at.desc())
    rows = session.execute(stmt).all()
    papers = get_papers(session, [row.SavedPaperRow.paper_id for row in rows])
    entries: list[SavedEntry] = []
    for row in rows:
        saved = row.SavedPaperRow
        paper = papers.get(saved.paper_id)
        if paper is None:
            continue
        entries.append(
            SavedEntry(
                paper=paper,
                status=saved.status,
                tags=list(saved.tags or []),
                note=saved.note,
                saved_at=saved.saved_at,
            )
        )
    return entries


def saved_tags(session: Session, user_sub: str) -> list[str]:
    """Every tag this reader has used, alphabetically - the chips over the library."""
    rows = session.execute(
        select(SavedPaperRow.tags).where(
            SavedPaperRow.user_sub == user_sub, SavedPaperRow.tags.is_not(None)
        )
    ).scalars()
    seen: set[str] = set()
    for tags in rows:
        seen.update(tags or [])
    return sorted(seen)


def saved_vectors(
    session: Session, user_sub: str, model_id: str
) -> list[tuple[str, str, datetime, list[float]]]:
    """(paper_id, title, saved_at, embedding) for the user's saved papers, newest save first.

    Only papers embedded under ``model_id`` return — the profile must live in one vector space.
    """
    rows = session.execute(
        select(
            SavedPaperRow.paper_id,
            PaperRow.title,
            SavedPaperRow.saved_at,
            PaperEmbeddingRow.embedding,
        )
        .join(PaperRow, PaperRow.id == SavedPaperRow.paper_id)
        .join(
            PaperEmbeddingRow,
            (PaperEmbeddingRow.paper_id == SavedPaperRow.paper_id)
            & (PaperEmbeddingRow.model_id == model_id),
        )
        .where(SavedPaperRow.user_sub == user_sub)
        .order_by(SavedPaperRow.saved_at.desc())
    ).all()
    return [
        (paper_id, title, saved_at, list(embedding))
        for paper_id, title, saved_at, embedding in rows
    ]


def saved_ids(session: Session, user_sub: str, paper_ids: list[str]) -> set[str]:
    """Which of ``paper_ids`` this user has saved (for annotating a feed page)."""
    if not paper_ids:
        return set()
    rows = session.execute(
        select(SavedPaperRow.paper_id).where(
            SavedPaperRow.user_sub == user_sub, SavedPaperRow.paper_id.in_(paper_ids)
        )
    ).scalars()
    return set(rows)
