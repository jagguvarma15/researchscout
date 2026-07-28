"""Breakthrough scoring: turn a paper's signal history into one momentum-aware number.

Breakthrough versus noise is a momentum question, not only a level one — a paper whose citations,
trending rank, and code stars are climbing fast is a stronger signal than one with high but static
counts. For each signal type this combines the current level with its velocity and acceleration,
orients rank-like signals (where lower is better) correctly, weights the types, and sums them.

The result is a non-negative boost with two deliberate special cases: it is 0 when a paper has no
signal history, so ranking falls back to pure recency; and it equals ``log1p(citations)`` for a
paper with only static citations — exactly the previous authority prior, recovered as a special
case, so existing behaviour is preserved until momentum data accrues.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from researchscout.config import get_settings
from researchscout.store.signals import all_series, all_series_many

_Series = list[tuple[datetime, float]]


@dataclass(frozen=True)
class SignalSpec:
    """How one signal type contributes: its weight and whether higher or lower values are better."""

    weight: float
    direction: float  # +1: higher value is better (counts); -1: lower is better (rank)


# Per-type defaults. hf_trending_rank is weighted up as a fast ignition proxy and inverted (rank 1
# is best); the not-yet-produced types are pre-registered so enabling their sources needs no change.
_SPECS: dict[str, SignalSpec] = {
    "citation": SignalSpec(weight=1.0, direction=1.0),
    "code_stars": SignalSpec(weight=1.0, direction=1.0),
    "hf_trending_rank": SignalSpec(weight=1.5, direction=-1.0),
    "social_mention": SignalSpec(weight=1.0, direction=1.0),
    "review_score": SignalSpec(weight=0.75, direction=1.0),
    "discussion": SignalSpec(weight=0.5, direction=1.0),
}


@dataclass(frozen=True)
class ScoreConfig:
    window_days: int
    velocity_weight: float
    acceleration_weight: float

    @classmethod
    def from_settings(cls) -> ScoreConfig:
        settings = get_settings()
        return cls(
            window_days=settings.score_window_days,
            velocity_weight=settings.score_velocity_weight,
            acceleration_weight=settings.score_acceleration_weight,
        )


@dataclass
class Breakthrough:
    total: float
    contributions: dict[str, float]


def _signed_log(x: float) -> float:
    """log1p that keeps its argument's sign, so a declining signal contributes negatively."""
    return math.copysign(math.log1p(abs(x)), x)


def _level(points: _Series) -> float:
    """The most recent observed value in the series (0 when empty)."""
    return points[-1][1] if points else 0.0


def _slope(points: _Series) -> float:
    """First derivative in value/day across the series; 0 under two points or zero span."""
    if len(points) < 2:
        return 0.0
    (first_at, first_value), (last_at, last_value) = points[0], points[-1]
    span_days = (last_at - first_at).total_seconds() / 86400.0
    return (last_value - first_value) / span_days if span_days > 0 else 0.0


def _acceleration(points: _Series) -> float:
    """Change in slope between the window's two halves; 0 under three points or zero span."""
    if len(points) < 3:
        return 0.0
    mid = len(points) // 2
    early, late = _slope(points[: mid + 1]), _slope(points[mid:])
    span_days = (points[-1][0] - points[0][0]).total_seconds() / 86400.0
    return (late - early) / span_days if span_days > 0 else 0.0


def score_signal(
    level: float,
    velocity: float,
    acceleration: float,
    *,
    spec: SignalSpec,
    config: ScoreConfig,
) -> float:
    """One signal type's contribution from its level, velocity, and acceleration (pure)."""
    if spec.direction >= 0:
        level_term = math.log1p(max(level, 0.0))
        velocity_term = _signed_log(velocity)
        acceleration_term = _signed_log(acceleration)
    else:
        # Rank-like: lower is better, and it counts only while the paper is on the list.
        level_term = 1.0 / level if level > 0 else 0.0
        velocity_term = _signed_log(-velocity)
        acceleration_term = _signed_log(-acceleration)
    return spec.weight * (
        level_term
        + config.velocity_weight * velocity_term
        + config.acceleration_weight * acceleration_term
    )


def _from_grouped(grouped: dict[str, _Series], cfg: ScoreConfig) -> Breakthrough:
    contributions: dict[str, float] = {}
    for type_name, spec in _SPECS.items():
        points = grouped.get(type_name)
        if not points:
            continue
        contribution = score_signal(
            _level(points), _slope(points), _acceleration(points), spec=spec, config=cfg
        )
        if contribution != 0.0:
            contributions[type_name] = contribution
    return Breakthrough(total=max(0.0, sum(contributions.values())), contributions=contributions)


def breakthrough(
    session: Session, paper_id: str, *, config: ScoreConfig | None = None
) -> Breakthrough:
    """Combine every signal type's momentum for one paper into a non-negative breakthrough boost."""
    cfg = config or ScoreConfig.from_settings()
    since = datetime.now(UTC) - timedelta(days=cfg.window_days)
    return _from_grouped(all_series(session, paper_id, since), cfg)


def breakthrough_many(
    session: Session, paper_ids: Sequence[str], *, config: ScoreConfig | None = None
) -> dict[str, Breakthrough]:
    """Breakthrough for many papers with one signals query; no history scores 0 as usual."""
    cfg = config or ScoreConfig.from_settings()
    since = datetime.now(UTC) - timedelta(days=cfg.window_days)
    grouped = all_series_many(session, paper_ids, since)
    return {paper_id: _from_grouped(grouped.get(paper_id, {}), cfg) for paper_id in paper_ids}
