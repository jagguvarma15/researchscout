"""Pipeline observability: hourly rollups from the lineage table.

This is the queryable-stream-state surface: every packet already lands in Postgres
idempotently, so a SQL aggregate is exactly-once by construction and survives restarts
with no recovery state of its own.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from researchscout.api.deps import get_session
from researchscout.api.schemas import StreamStatBucket, StreamStats
from researchscout.store.lineage import hourly_stats

router = APIRouter(tags=["stream"])


@router.get("/stream/stats")
def stream_stats(
    session: Annotated[Session, Depends(get_session)],
    hours: Annotated[int, Query(ge=1, le=720)] = 24,
) -> StreamStats:
    """Per-stage hourly throughput, outcomes, and latency for the streaming pipeline."""
    buckets = [StreamStatBucket.model_validate(row) for row in hourly_stats(session, hours=hours)]
    return StreamStats(hours=hours, buckets=buckets)
