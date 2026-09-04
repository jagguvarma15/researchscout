"""Per-request For You feed metrics for the dashboards.

One row per render: the window served, the profile shape, and the segment latencies. Recording
is best-effort in a session of its own, after the response is built, exactly like ask_metrics -
a metrics failure must never surface to the reader. Retention is bounded by
:func:`prune_feed_metrics`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from researchscout.store.models import FeedMetricRow

# How much history stays; latency samples, not a permanent record.
_KEEP_DAYS = 90


@dataclass(frozen=True)
class FeedSummary:
    """For You render latency over a recent window, for the status payload."""

    days: int
    requests: int
    p50_ms: int | None
    p95_ms: int | None
    cache_hit_rate: float | None


def feed_summary(session: Session, *, days: int = 7) -> FeedSummary:
    """Count, total-latency percentiles, and profile-cache hit rate over the window."""
    since = datetime.now(UTC) - timedelta(days=days)
    requests, hit_rate = session.execute(
        select(
            func.count(),
            func.avg(case((FeedMetricRow.profile_cache_hit, 1.0), else_=0.0)),
        ).where(FeedMetricRow.requested_at >= since)
    ).one()
    p50, p95 = session.execute(
        select(
            func.percentile_cont(0.5).within_group(FeedMetricRow.total_ms),
            func.percentile_cont(0.95).within_group(FeedMetricRow.total_ms),
        ).where(FeedMetricRow.requested_at >= since)
    ).one()
    return FeedSummary(
        days=days,
        requests=int(requests),
        p50_ms=int(p50) if p50 is not None else None,
        p95_ms=int(p95) if p95 is not None else None,
        cache_hit_rate=float(hit_rate) if hit_rate is not None else None,
    )


def record_feed(
    session: Session,
    *,
    user_hash: str | None,
    days: int,
    k: int,
    centroids: int,
    candidates: int,
    returned: int,
    profile_cache_hit: bool,
    profile_ms: int | None,
    search_ms: int | None,
    signals_ms: int | None,
    rank_ms: int | None,
    total_ms: int,
) -> None:
    """Append one metrics row (the caller owns the session and its lifecycle)."""
    session.add(
        FeedMetricRow(
            user_hash=user_hash,
            days=days,
            k=k,
            centroids=centroids,
            candidates=candidates,
            returned=returned,
            profile_cache_hit=profile_cache_hit,
            profile_ms=profile_ms,
            search_ms=search_ms,
            signals_ms=signals_ms,
            rank_ms=rank_ms,
            total_ms=total_ms,
        )
    )
    session.flush()


def prune_feed_metrics(session: Session, *, keep_days: int = _KEEP_DAYS) -> None:
    """Drop metrics rows older than the retention window."""
    cutoff = datetime.now(UTC) - timedelta(days=keep_days)
    session.execute(delete(FeedMetricRow).where(FeedMetricRow.requested_at < cutoff))
