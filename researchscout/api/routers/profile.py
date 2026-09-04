"""The caller's profile: research interests that steer what the radar surfaces."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_session
from researchscout.api.schemas import InterestList, InterestUpdate
from researchscout.retrieve.profile_cache import invalidate
from researchscout.store.interests import get_interests, set_interests

router = APIRouter(tags=["profile"])


@router.get("/me/interests")
def my_interests(
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> InterestList:
    """The caller's research interests."""
    return InterestList(interests=get_interests(session, user.sub))


@router.put("/me/interests")
def update_interests(
    body: InterestUpdate,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> InterestList:
    """Replace the caller's research interests wholesale."""
    interests = set_interests(session, user.sub, body.interests)
    invalidate(user.sub)  # interests anchor the profile; the next feed request rebuilds it
    return InterestList(interests=interests)
