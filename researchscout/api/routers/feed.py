"""The authenticated personalized feed, ranked by the caller's interests."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_embedder, get_session
from researchscout.api.schemas import PaperList, PaperSummary
from researchscout.config import get_settings
from researchscout.embed.base import Embedder
from researchscout.retrieve.personalize import personalized_papers
from researchscout.store.interests import get_interests

router = APIRouter(tags=["feed"])


@router.get("/me/feed")
def my_feed(
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    days: Annotated[int | None, Query(ge=1, le=365)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaperList:
    """Papers ranked by the caller's interests; empty when they have none."""
    window = days if days is not None else get_settings().freshness_days
    results = personalized_papers(
        session,
        embedder,
        get_interests(session, user.sub),
        user_sub=user.sub,
        k=limit,
        days=window,
    )
    return PaperList(
        items=[
            PaperSummary.from_paper(item.paper, score=item.score, reason=item.reason)
            for item in results
        ]
    )
