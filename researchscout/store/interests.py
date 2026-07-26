"""Per-user research interests. Identity is the caller's ``sub`` claim — no local user table."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from researchscout.store.models import UserInterestRow

MAX_INTERESTS = 20


def get_interests(session: Session, user_sub: str) -> list[str]:
    """A user's interests, oldest first (ties broken alphabetically for stable pages)."""
    rows = session.execute(
        select(UserInterestRow.interest)
        .where(UserInterestRow.user_sub == user_sub)
        .order_by(UserInterestRow.created_at, UserInterestRow.interest)
    ).scalars()
    return list(rows)


def set_interests(session: Session, user_sub: str, interests: list[str]) -> list[str]:
    """Replace a user's interests wholesale; returns the cleaned list that was stored.

    Cleaning drops blanks and duplicates (keeping first occurrence) and caps the list
    at ``MAX_INTERESTS``.
    """
    cleaned: list[str] = []
    for raw in interests:
        interest = raw.strip()
        if interest and interest not in cleaned:
            cleaned.append(interest)
    cleaned = cleaned[:MAX_INTERESTS]
    session.execute(delete(UserInterestRow).where(UserInterestRow.user_sub == user_sub))
    session.add_all(UserInterestRow(user_sub=user_sub, interest=interest) for interest in cleaned)
    return cleaned
