"""The authenticated reading list: save/unsave papers and list what you saved."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_session
from researchscout.api.schemas import PaperList, PaperSummary
from researchscout.events.publish import publish_paper_saved
from researchscout.store.papers import get_paper
from researchscout.store.saved import list_saved, save_paper, unsave_paper

router = APIRouter(tags=["saved"])


@router.post("/papers/{paper_id:path}/save")
def save(
    paper_id: str,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, bool]:
    """Add a paper to the caller's reading list (idempotent)."""
    if get_paper(session, paper_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown paper id: {paper_id}")
    created = save_paper(session, user.sub, paper_id)
    if created:
        publish_paper_saved(user.sub, paper_id, True)
    return {"saved": True}


@router.delete("/papers/{paper_id:path}/save")
def unsave(
    paper_id: str,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, bool]:
    """Remove a paper from the caller's reading list (idempotent)."""
    removed = unsave_paper(session, user.sub, paper_id)
    if removed:
        publish_paper_saved(user.sub, paper_id, False)
    return {"saved": False}


@router.get("/me/saved")
def my_saved(
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> PaperList:
    """The caller's reading list, most recently saved first."""
    return PaperList(items=[PaperSummary.from_paper(p) for p in list_saved(session, user.sub)])
