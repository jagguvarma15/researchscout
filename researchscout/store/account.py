"""Per-account site state: recent searches, recently opened papers, dismissals, filters.

A cache with an account attached. Everything here can be thrown away without losing anything
that matters -- the tables are UNLOGGED (migration 0021), so Postgres will do exactly that after
an unclean stop -- and everything here is written on an ordinary page interaction, which is why
it is worth the tables not writing WAL.

Two rules hold across all four:

* **Capped on write.** Recording the twenty-first search deletes the oldest, in the same
  statement. A cache that only ever grows is a table with extra steps, and this one is written
  on a keystroke.
* **Signed in only.** Every function takes a ``sub``; the API routes above them all require an
  account. A signed-out visitor gets the same site with no memory, which is the honest version
  of not having anywhere to put it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from researchscout.store.models import (
    AccountDismissalRow,
    AccountFilterRow,
    AccountRecentPaperRow,
    AccountSearchRow,
    PaperRow,
)

MAX_SEARCHES = 20
MAX_RECENT_PAPERS = 20
MAX_DISMISSALS = 200


def _now() -> datetime:
    return datetime.now(UTC)


def record_search(session: Session, sub: str, query: str, *, cap: int = MAX_SEARCHES) -> None:
    """Remember a search, most recent first, keeping at most ``cap`` per account.

    Searching the same phrase twice moves it up rather than adding a second copy, so a list of
    twenty is twenty different things somebody looked for.
    """
    trimmed = query.strip()[:200]
    if not trimmed:
        return
    stmt = insert(AccountSearchRow).values(user_sub=sub, query=trimmed, searched_at=_now())
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_sub", "query"],
            set_={"searched_at": stmt.excluded.searched_at},
        )
    )
    keep = (
        select(AccountSearchRow.id)
        .where(AccountSearchRow.user_sub == sub)
        .order_by(AccountSearchRow.searched_at.desc(), AccountSearchRow.id.desc())
        .limit(cap)
    )
    session.execute(
        delete(AccountSearchRow).where(
            AccountSearchRow.user_sub == sub,
            AccountSearchRow.id.not_in(keep.scalar_subquery()),
        )
    )


def recent_searches(session: Session, sub: str, *, limit: int = MAX_SEARCHES) -> list[str]:
    """This account's searches, most recent first."""
    return list(
        session.execute(
            select(AccountSearchRow.query)
            .where(AccountSearchRow.user_sub == sub)
            .order_by(AccountSearchRow.searched_at.desc(), AccountSearchRow.id.desc())
            .limit(limit)
        ).scalars()
    )


def clear_searches(session: Session, sub: str) -> int:
    """Forget every search this account made; returns how many went."""
    result = session.execute(delete(AccountSearchRow).where(AccountSearchRow.user_sub == sub))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def record_view(session: Session, sub: str, paper_id: str, *, cap: int = MAX_RECENT_PAPERS) -> None:
    """Remember that this account opened a paper, keeping at most ``cap`` of them."""
    if not _paper_exists(session, paper_id):
        return  # the same best-effort contract the events beacon has
    stmt = insert(AccountRecentPaperRow).values(user_sub=sub, paper_id=paper_id, viewed_at=_now())
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_sub", "paper_id"],
            set_={"viewed_at": stmt.excluded.viewed_at},
        )
    )
    keep = (
        select(AccountRecentPaperRow.paper_id)
        .where(AccountRecentPaperRow.user_sub == sub)
        .order_by(AccountRecentPaperRow.viewed_at.desc())
        .limit(cap)
    )
    session.execute(
        delete(AccountRecentPaperRow).where(
            AccountRecentPaperRow.user_sub == sub,
            AccountRecentPaperRow.paper_id.not_in(keep.scalar_subquery()),
        )
    )


def recent_papers(session: Session, sub: str, *, limit: int = MAX_RECENT_PAPERS) -> list[str]:
    """Paper ids this account opened, most recent first."""
    return list(
        session.execute(
            select(AccountRecentPaperRow.paper_id)
            .where(AccountRecentPaperRow.user_sub == sub)
            .order_by(AccountRecentPaperRow.viewed_at.desc())
            .limit(limit)
        ).scalars()
    )


def record_dismissal(
    session: Session, sub: str, paper_id: str, *, cap: int = MAX_DISMISSALS
) -> None:
    """Remember that this account sent a paper to the end of the feed."""
    if not _paper_exists(session, paper_id):
        return
    stmt = insert(AccountDismissalRow).values(user_sub=sub, paper_id=paper_id, dismissed_at=_now())
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_sub", "paper_id"],
            set_={"dismissed_at": stmt.excluded.dismissed_at},
        )
    )
    keep = (
        select(AccountDismissalRow.paper_id)
        .where(AccountDismissalRow.user_sub == sub)
        .order_by(AccountDismissalRow.dismissed_at.desc())
        .limit(cap)
    )
    session.execute(
        delete(AccountDismissalRow).where(
            AccountDismissalRow.user_sub == sub,
            AccountDismissalRow.paper_id.not_in(keep.scalar_subquery()),
        )
    )


def dismissed_papers(session: Session, sub: str, *, limit: int = MAX_DISMISSALS) -> list[str]:
    """Paper ids this account pushed to the end of the feed, most recent first."""
    return list(
        session.execute(
            select(AccountDismissalRow.paper_id)
            .where(AccountDismissalRow.user_sub == sub)
            .order_by(AccountDismissalRow.dismissed_at.desc())
            .limit(limit)
        ).scalars()
    )


def restore_dismissed(session: Session, sub: str, paper_ids: Sequence[str] | None = None) -> int:
    """Undo dismissals: the named papers, or all of them when none are named."""
    stmt = delete(AccountDismissalRow).where(AccountDismissalRow.user_sub == sub)
    if paper_ids is not None:
        stmt = stmt.where(AccountDismissalRow.paper_id.in_(list(paper_ids)))
    return int(session.execute(stmt).rowcount or 0)  # type: ignore[attr-defined]


def save_filters(session: Session, sub: str, query_string: str) -> None:
    """Remember the feed's query string, so the next visit opens where this one left off."""
    stmt = insert(AccountFilterRow).values(
        user_sub=sub, query_string=query_string[:2000], saved_at=_now()
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_sub"],
            set_={
                "query_string": stmt.excluded.query_string,
                "saved_at": stmt.excluded.saved_at,
            },
        )
    )


def saved_filters(session: Session, sub: str) -> str | None:
    """The last filter query string, or None when this account has not set one."""
    return session.execute(
        select(AccountFilterRow.query_string).where(AccountFilterRow.user_sub == sub)
    ).scalar_one_or_none()


def export(session: Session, sub: str) -> dict[str, object]:
    """Everything cached about one account, for the data export."""
    searches = session.execute(
        select(AccountSearchRow.query, AccountSearchRow.searched_at)
        .where(AccountSearchRow.user_sub == sub)
        .order_by(AccountSearchRow.searched_at.desc())
    ).all()
    viewed = session.execute(
        select(AccountRecentPaperRow.paper_id, AccountRecentPaperRow.viewed_at)
        .where(AccountRecentPaperRow.user_sub == sub)
        .order_by(AccountRecentPaperRow.viewed_at.desc())
    ).all()
    dismissed = session.execute(
        select(AccountDismissalRow.paper_id, AccountDismissalRow.dismissed_at)
        .where(AccountDismissalRow.user_sub == sub)
        .order_by(AccountDismissalRow.dismissed_at.desc())
    ).all()
    return {
        "recent_searches": [
            {"query": query, "searched_at": at.isoformat()} for query, at in searches
        ],
        "recent_papers": [
            {"paper_id": paper_id, "viewed_at": at.isoformat()} for paper_id, at in viewed
        ],
        "dismissed_papers": [
            {"paper_id": paper_id, "dismissed_at": at.isoformat()} for paper_id, at in dismissed
        ],
        "saved_filters": saved_filters(session, sub),
    }


def forget(session: Session, sub: str) -> None:
    """Drop every cached row for one account.

    The 0021 cascade covers account deletion; this is for the case where somebody used the API
    without ever creating an account row, which is the same gap ``delete_user`` closes.
    """
    for model in (AccountSearchRow, AccountRecentPaperRow, AccountDismissalRow, AccountFilterRow):
        session.execute(delete(model).where(model.user_sub == sub))


def _paper_exists(session: Session, paper_id: str) -> bool:
    """Whether the paper is known, so a stale id from an open tab is dropped, not an error."""
    return (
        session.execute(
            select(func.count()).select_from(PaperRow).where(PaperRow.id == paper_id)
        ).scalar_one()
        > 0
    )
