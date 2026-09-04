"""The authenticated personalized feed, ranked by the caller's interests."""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from researchscout.api.auth import User, owner_tag, require_user
from researchscout.api.deps import get_embedder, get_session
from researchscout.api.schemas import FeedProfileInfo, FeedResponse, PaperSummary
from researchscout.config import get_settings
from researchscout.embed.base import Embedder
from researchscout.retrieve.personalize import personalized_papers
from researchscout.store.interests import get_interests
from researchscout.trace import trace_span

logger = logging.getLogger(__name__)

router = APIRouter(tags=["feed"])


def _record(**fields: Any) -> None:
    """Best-effort feed metrics in a session of its own; a failure never reaches the reader."""
    from researchscout.store.db import session_scope
    from researchscout.store.feed_metrics import record_feed

    try:
        with session_scope() as session:
            record_feed(session, **fields)
    except Exception:  # noqa: BLE001 - metrics are never worth an error
        logger.warning("could not record feed metrics", exc_info=True)


@router.get("/me/feed")
def my_feed(
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    days: Annotated[int | None, Query(ge=1, le=365)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> FeedResponse:
    """Papers ranked by the caller's interests; empty when they have none."""
    window = days if days is not None else get_settings().freshness_days
    timings: dict[str, float] = {}
    profile: dict[str, int] = {}
    started = perf_counter()
    with trace_span("feed", days=window, k=limit) as span:
        results = personalized_papers(
            session,
            embedder,
            get_interests(session, user.sub),
            user_sub=user.sub,
            k=limit,
            days=window,
            timings=timings,
            profile=profile,
        )
        span.update({key: value for key, value in timings.items()})
    total_ms = int((perf_counter() - started) * 1000)

    _record(
        user_hash=owner_tag(user.sub),
        days=window,
        k=limit,
        centroids=profile.get("centroids", 0),
        candidates=int(timings.get("candidates", 0)),
        returned=len(results),
        profile_cache_hit=timings.get("cache_hit") == 1.0,
        profile_ms=int(timings["profile_ms"]) if "profile_ms" in timings else None,
        search_ms=int(timings["search_ms"]) if "search_ms" in timings else None,
        signals_ms=int(timings["signals_ms"]) if "signals_ms" in timings else None,
        rank_ms=int(timings["rank_ms"]) if "rank_ms" in timings else None,
        total_ms=total_ms,
    )

    return FeedResponse(
        items=[
            PaperSummary.from_paper(item.paper, score=item.score, reason=item.reason)
            for item in results
        ],
        profile=FeedProfileInfo(**profile) if profile else None,
    )
