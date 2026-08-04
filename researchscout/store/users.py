"""Accounts: the row behind a token's ``sub``.

Everything a signed-in person owns hangs off that string - saved papers, interests, reading
events. This module is the one place that creates the account row, records which terms version
they accepted, hands their data back, and deletes all of it. Deletion relies on the foreign
keys added in migration 0019, so a new user-scoped table cascades without touching this file.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from researchscout.store import account as account_cache
from researchscout.store.models import EventRow, SavedPaperRow, UserInterestRow, UserRow

# Below this age a repeat visit does not rewrite last_seen_at: the column exists to spot
# dormant accounts, not to count requests.
_LAST_SEEN_STALE = timedelta(hours=1)


def upsert_user(
    session: Session,
    sub: str,
    *,
    email: str | None = None,
    display_name: str | None = None,
) -> None:
    """Ensure the account exists and keep its identity claims fresh.

    Called on every authenticated request, so the update is deliberately narrow: claims are
    written back only when the provider actually sent them, and only once last_seen_at has
    gone stale, which keeps steady browsing from writing a row per request.
    """
    stmt = insert(UserRow).values(sub=sub, email=email, display_name=display_name)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["sub"],
            set_={
                "email": stmt.excluded.email if email is not None else UserRow.email,
                "display_name": (
                    stmt.excluded.display_name if display_name is not None else UserRow.display_name
                ),
                "last_seen_at": func.now(),
            },
            where=UserRow.last_seen_at < func.now() - _LAST_SEEN_STALE,
        )
    )


def get_user(session: Session, sub: str) -> UserRow | None:
    """The account row, or None when nobody has signed in under that sub yet."""
    return session.get(UserRow, sub)


def accept_terms(session: Session, sub: str, version: str) -> UserRow:
    """Record acceptance of a terms version; accepting a newer one just moves the mark."""
    user = session.get(UserRow, sub)
    if user is None:
        user = UserRow(sub=sub)
        session.add(user)
    user.tos_version = version
    user.tos_accepted_at = datetime.now(UTC)
    session.flush()
    return user


def export_user_data(session: Session, sub: str) -> dict[str, Any]:
    """Everything stored about one account, as plain JSON-ready data."""
    user = session.get(UserRow, sub)
    saved = session.execute(
        select(SavedPaperRow.paper_id, SavedPaperRow.saved_at)
        .where(SavedPaperRow.user_sub == sub)
        .order_by(SavedPaperRow.saved_at)
    ).all()
    interests = session.execute(
        select(UserInterestRow.interest, UserInterestRow.created_at)
        .where(UserInterestRow.user_sub == sub)
        .order_by(UserInterestRow.created_at)
    ).all()
    events = session.execute(
        select(EventRow.event, EventRow.paper_id, EventRow.surface, EventRow.occurred_at)
        .where(EventRow.user_sub == sub)
        .order_by(EventRow.occurred_at)
    ).all()
    return {
        "account": {
            "sub": sub,
            "email": user.email if user else None,
            "display_name": user.display_name if user else None,
            "created_at": _iso(user.created_at) if user else None,
            "terms_version": user.tos_version if user else None,
            "terms_accepted_at": _iso(user.tos_accepted_at) if user else None,
        },
        "saved_papers": [
            {"paper_id": paper_id, "saved_at": _iso(saved_at)} for paper_id, saved_at in saved
        ],
        "interests": [
            {"interest": interest, "created_at": _iso(created_at)}
            for interest, created_at in interests
        ],
        "reading_events": [
            {
                "event": event,
                "paper_id": paper_id,
                "surface": surface,
                "occurred_at": _iso(occurred_at),
            }
            for event, paper_id, surface, occurred_at in events
        ],
        # Cached rather than kept, but stored about this person all the same, so the export
        # the privacy notice promises has to include it.
        "site_state": account_cache.export(session, sub),
    }


def delete_user(session: Session, sub: str) -> bool:
    """Delete the account and everything hanging off it; False when there was no account row.

    Saved papers, interests and events go with it through the 0019 cascade. Rows that are not
    about a person - papers, signals, ask metrics - are untouched: they are the corpus, not the
    account. The scoped deletes below cover the case where someone used the API without ever
    creating an account row, so "delete my data" stays true either way.
    """
    session.execute(delete(SavedPaperRow).where(SavedPaperRow.user_sub == sub))
    session.execute(delete(UserInterestRow).where(UserInterestRow.user_sub == sub))
    session.execute(delete(EventRow).where(EventRow.user_sub == sub))
    account_cache.forget(session, sub)
    existed = session.get(UserRow, sub) is not None
    session.execute(delete(UserRow).where(UserRow.sub == sub))
    return existed


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
