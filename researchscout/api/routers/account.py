"""What the site remembers for a signed-in visitor between page loads.

Recent searches, recently opened papers, dismissals and the last filter state. All of it is a
cache (see ``researchscout/store/account.py``), all of it requires an account, and all of it is
best-effort on the way in: a beacon that fails costs a suggestion, never a page.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_session
from researchscout.api.schemas import (
    DismissalList,
    DismissRequest,
    FilterState,
    PaperSummary,
    RecentPaperList,
    SearchHistory,
    SearchRecord,
    ViewRecord,
)
from researchscout.store import account
from researchscout.store.papers import get_papers

router = APIRouter(tags=["account"])


@router.get("/me/history")
def my_history(
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> SearchHistory:
    """Phrases this account searched for, most recent first."""
    return SearchHistory(items=account.recent_searches(session, user.sub))


@router.post("/me/history", status_code=202)
def record_search(
    body: SearchRecord,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> SearchHistory:
    """Remember a search. Repeating one moves it up rather than adding a duplicate."""
    account.record_search(session, user.sub, body.query)
    return SearchHistory(items=account.recent_searches(session, user.sub))


@router.delete("/me/history")
def clear_history(
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> SearchHistory:
    """Forget every search. The site keeps working; it just stops suggesting."""
    account.clear_searches(session, user.sub)
    return SearchHistory(items=[])


def _recent(session: Session, sub: str) -> RecentPaperList:
    """This account's recently opened papers, newest first, as something showable.

    Hydrated rather than returned as bare ids: the only thing anyone does with this list is put
    it in front of a reader, and "arxiv:2504.01234" is not a thing anyone recognises. The order
    is the cache's, which the id-keyed hydration does not preserve, so it is reapplied here.
    """
    ids = account.recent_papers(session, sub)
    if not ids:
        return RecentPaperList(items=[])
    papers = get_papers(session, ids)
    return RecentPaperList(
        items=[PaperSummary.from_paper(papers[pid]) for pid in ids if pid in papers]
    )


@router.get("/me/recent")
def my_recent_papers(
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> RecentPaperList:
    """Papers this account opened, most recent first."""
    return _recent(session, user.sub)


@router.post("/me/recent", status_code=202)
def record_view(
    body: ViewRecord,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> RecentPaperList:
    """Remember that a paper was opened; an unknown id is dropped rather than refused."""
    account.record_view(session, user.sub, body.paper_id)
    return _recent(session, user.sub)


@router.get("/me/dismissals")
def my_dismissals(
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> DismissalList:
    """Papers this account sent to the end of the feed."""
    return DismissalList(items=account.dismissed_papers(session, user.sub))


@router.post("/me/dismissals", status_code=202)
def dismiss(
    body: DismissRequest,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> DismissalList:
    """Send a paper to the end of the feed. It is not hidden and stays reachable everywhere."""
    account.record_dismissal(session, user.sub, body.paper_id)
    return DismissalList(items=account.dismissed_papers(session, user.sub))


@router.delete("/me/dismissals")
def restore_dismissals(
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
    paper_id: str | None = None,
) -> DismissalList:
    """Bring one paper back to its place in the feed, or all of them."""
    account.restore_dismissed(session, user.sub, [paper_id] if paper_id else None)
    return DismissalList(items=account.dismissed_papers(session, user.sub))


@router.get("/me/filters")
def my_filters(
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> FilterState:
    """The feed query string this account last applied, or null."""
    return FilterState(query_string=account.saved_filters(session, user.sub))


@router.put("/me/filters")
def save_filters(
    body: FilterState,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> FilterState:
    """Remember the feed's filter state so the next visit opens where this one left off."""
    account.save_filters(session, user.sub, body.query_string or "")
    return FilterState(query_string=account.saved_filters(session, user.sub))
