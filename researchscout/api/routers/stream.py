"""Pipeline observability: hourly rollups from the lineage table.

This is the queryable-stream-state surface: every packet already lands in Postgres
idempotently, so a SQL aggregate is exactly-once by construction and survives restarts
with no recovery state of its own.

Signed in only, unlike the other read routes: this is operational detail about the machine
behind the site - throughput, failures, how far behind the pipeline is - and none of it is
anybody else's business.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_session
from researchscout.api.schemas import StreamStatBucket, StreamStats
from researchscout.store.lineage import hourly_stats

router = APIRouter(tags=["stream"])


@router.get("/stream/stats")
def stream_stats(
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
    hours: Annotated[int, Query(ge=1, le=720)] = 24,
) -> StreamStats:
    """Per-stage hourly throughput, outcomes, and latency for the streaming pipeline."""
    buckets = [StreamStatBucket.model_validate(row) for row in hourly_stats(session, hours=hours)]
    return StreamStats(hours=hours, buckets=buckets)
