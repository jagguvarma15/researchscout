from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from researchscout.store.feed_metrics import (
    feed_summary,
    prune_feed_metrics,
    record_feed,
)
from researchscout.store.models import FeedMetricRow

pytestmark = pytest.mark.integration


def _record(
    session: Session,
    *,
    total_ms: int = 100,
    profile_cache_hit: bool = True,
    days: int = 30,
) -> None:
    record_feed(
        session,
        user_hash="abc123def456",
        days=days,
        k=20,
        centroids=3,
        candidates=120,
        returned=20,
        profile_cache_hit=profile_cache_hit,
        profile_ms=5,
        search_ms=40,
        signals_ms=30,
        rank_ms=10,
        total_ms=total_ms,
    )


def test_record_feed_lands_a_row(session: Session) -> None:
    _record(session, total_ms=250)
    row = session.execute(select(FeedMetricRow)).scalar_one()
    assert row.days == 30 and row.k == 20 and row.centroids == 3
    assert row.candidates == 120 and row.returned == 20
    assert row.profile_cache_hit is True and row.total_ms == 250
    assert row.user_hash == "abc123def456"  # the tag, never the sub
    assert row.requested_at >= datetime.now(UTC) - timedelta(minutes=1)


def test_feed_summary_percentiles_and_cache_rate(session: Session) -> None:
    for total in (100, 200, 300, 400):
        _record(session, total_ms=total, profile_cache_hit=True)
    _record(session, total_ms=2000, profile_cache_hit=False)

    summary = feed_summary(session, days=7)
    assert summary.requests == 5
    assert summary.p50_ms == 300
    assert summary.p95_ms == pytest.approx(1680, abs=1)
    assert summary.cache_hit_rate == pytest.approx(0.8)


def test_feed_summary_empty_window(session: Session) -> None:
    summary = feed_summary(session, days=7)
    assert summary.requests == 0
    assert summary.p50_ms is None and summary.p95_ms is None
    assert summary.cache_hit_rate is None


def test_prune_feed_metrics_drops_only_old_rows(session: Session) -> None:
    _record(session, days=7)  # marker for the row to age
    _record(session, days=30)  # marker for the row to keep
    session.flush()
    session.execute(
        update(FeedMetricRow)
        .where(FeedMetricRow.days == 7)
        .values(requested_at=datetime.now(UTC) - timedelta(days=120))
    )

    prune_feed_metrics(session, keep_days=90)
    kept = session.execute(select(FeedMetricRow.days)).scalars().all()
    assert kept == [30]
