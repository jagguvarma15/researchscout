"""The trends payload: where benchmark frontiers moved and what the majors shipped.

A public read like the catalogue routes it draws from - the same tables, arranged along
time instead of rank, so the /trends page can chart movement rather than standings.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from researchscout.api.deps import get_session
from researchscout.api.schemas import (
    NotableModelInfo,
    SotaPointInfo,
    SotaSeriesInfo,
    TrendsResponse,
)
from researchscout.providers import load_providers
from researchscout.store.catalog import recent_provider_models, sota_series

router = APIRouter(tags=["trends"])

# How far back the release timeline reaches, and how many models one lab may place on it.
_RELEASE_WINDOW_DAYS = 730
_RELEASES_PER_PROVIDER = 24


@router.get("/trends")
def trends(session: Annotated[Session, Depends(get_session)]) -> TrendsResponse:
    """Benchmark state-of-the-art over time plus the recent notable-model releases."""
    config = load_providers()
    return TrendsResponse(
        sota=[
            SotaSeriesInfo(
                id=series.id,
                name=series.name,
                scale=series.scale,
                points=[
                    SotaPointInfo(
                        on=point.on,
                        score=point.score,
                        model_name=point.model_name,
                        model_id=point.model_id,
                    )
                    for point in series.points
                ],
            )
            for series in sota_series(session, config)
        ],
        releases=[
            NotableModelInfo(
                id=item.id,
                name=item.name,
                provider=item.provider,
                country=item.country,
                published_on=item.published_on,
                parameters=item.parameters,
                open_weights=item.open_weights,
            )
            for item in recent_provider_models(
                session,
                config,
                since=date.today() - timedelta(days=_RELEASE_WINDOW_DAYS),
                per_provider=_RELEASES_PER_PROVIDER,
            )
        ],
    )
