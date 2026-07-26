"""Per-user reading list. Identity is the caller's ``sub`` claim — no local user table."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from researchscout.schema import Paper
from researchscout.store.models import SavedPaperRow
from researchscout.store.papers import get_paper


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


def list_saved(session: Session, user_sub: str) -> list[Paper]:
    """A user's saved papers, most recently saved first."""
    ids = session.execute(
        select(SavedPaperRow.paper_id)
        .where(SavedPaperRow.user_sub == user_sub)
        .order_by(SavedPaperRow.saved_at.desc())
    ).scalars()
    papers = (get_paper(session, paper_id) for paper_id in ids)
    return [paper for paper in papers if paper is not None]


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
