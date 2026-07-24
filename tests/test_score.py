import math
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from researchscout.score import (
    ScoreConfig,
    SignalSpec,
    _acceleration,
    _level,
    _slope,
    breakthrough,
    score_signal,
)

CFG = ScoreConfig(window_days=30, velocity_weight=2.0, acceleration_weight=1.0)
UP = SignalSpec(weight=1.0, direction=1.0)  # count-like: higher is better
RANK = SignalSpec(weight=1.5, direction=-1.0)  # rank-like: lower is better


def test_static_count_reduces_to_log1p() -> None:
    # Level only, no momentum: the prior authority factor, recovered exactly.
    assert score_signal(50.0, 0.0, 0.0, spec=UP, config=CFG) == math.log1p(50.0)


def test_rising_beats_flat_beats_declining() -> None:
    flat = score_signal(50.0, 0.0, 0.0, spec=UP, config=CFG)
    rising = score_signal(50.0, 5.0, 0.0, spec=UP, config=CFG)
    declining = score_signal(50.0, -5.0, 0.0, spec=UP, config=CFG)
    assert rising > flat > declining


def test_positive_acceleration_adds() -> None:
    steady = score_signal(50.0, 5.0, 0.0, spec=UP, config=CFG)
    accelerating = score_signal(50.0, 5.0, 3.0, spec=UP, config=CFG)
    assert accelerating > steady


def test_no_signal_scores_zero() -> None:
    assert score_signal(0.0, 0.0, 0.0, spec=UP, config=CFG) == 0.0


def test_rank_lower_is_better() -> None:
    top = score_signal(1.0, 0.0, 0.0, spec=RANK, config=CFG)  # rank 1
    low = score_signal(10.0, 0.0, 0.0, spec=RANK, config=CFG)  # rank 10
    assert top > low > 0.0


def test_rank_improving_is_positive_momentum() -> None:
    # Rank value falling (climbing the list) is good; rising is bad.
    improving = score_signal(3.0, -4.0, 0.0, spec=RANK, config=CFG)
    worsening = score_signal(3.0, 4.0, 0.0, spec=RANK, config=CFG)
    assert improving > worsening


def test_pure_series_helpers() -> None:
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    points = [(t0, 0.0), (t0 + timedelta(days=1), 2.0), (t0 + timedelta(days=2), 6.0)]
    assert _level(points) == 6.0
    assert _slope(points) == 3.0  # (6 - 0) / 2 days
    assert _acceleration(points) > 0.0  # 2/day then 4/day: speeding up
    assert _slope([points[0]]) == 0.0  # single point
    assert _acceleration(points[:2]) == 0.0  # under three points


@pytest.mark.integration
def test_breakthrough_rewards_momentum(session: Session) -> None:
    from researchscout.schema import Author, Paper, Signal, SignalType
    from researchscout.store.papers import upsert_paper
    from researchscout.store.signals import append_signal

    pid = "arxiv:2401.00001"
    upsert_paper(
        session,
        Paper(
            id=pid,
            external_ids={"arxiv": "2401.00001"},
            title="T",
            abstract="A",
            authors=[Author(name="X")],
            categories=["cs.LG"],
            published_at=datetime(2024, 1, 1, tzinfo=UTC),
            source="arxiv",
        ),
    )
    now = datetime.now(UTC)
    for days_ago, value in [(20, 10.0), (10, 30.0), (1, 80.0)]:
        append_signal(
            session,
            Signal(
                paper_id=pid,
                type=SignalType.citation,
                source="test",
                value=value,
                observed_at=now - timedelta(days=days_ago),
            ),
        )

    result = breakthrough(session, pid)
    assert result.total > math.log1p(80.0)  # rising series scores above its level alone
    assert "citation" in result.contributions
    assert breakthrough(session, "arxiv:9999.99999").total == 0.0  # no signals -> no boost
