"""Append-only signal time series: append observations, read a series, compute velocity.

Signals are never updated in place — each observation is a timestamped row, so deltas over time give
velocity (and, later at Stage 3, acceleration / ignition). This module provides the substrate and
the first derivative only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from researchscout.schema import Signal
from researchscout.store.models import SignalRow


def append_signal(session: Session, signal: Signal) -> None:
    """Append one observation (append-only; never updates an existing row)."""
    session.add(
        SignalRow(
            paper_id=signal.paper_id,
            type=str(signal.type),
            source=signal.source,
            value=signal.value,
            signal_metadata=signal.metadata,
            observed_at=signal.observed_at,
        )
    )
    session.flush()


def latest_value(session: Session, paper_id: str, type: str) -> float:
    """The most recent observed value for one paper+type (0 when unobserved)."""
    value = session.execute(
        select(SignalRow.value)
        .where(SignalRow.paper_id == paper_id, SignalRow.type == type)
        .order_by(SignalRow.observed_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return float(value) if value is not None else 0.0


def series(
    session: Session, paper_id: str, type: str, since: datetime
) -> list[tuple[datetime, float]]:
    """Return (observed_at, value) points for one paper+type since a time, oldest first."""
    rows = session.execute(
        select(SignalRow.observed_at, SignalRow.value)
        .where(
            SignalRow.paper_id == paper_id,
            SignalRow.type == type,
            SignalRow.observed_at >= since,
        )
        .order_by(SignalRow.observed_at)
    ).all()
    return [(observed_at, value) for observed_at, value in rows]


def velocity(session: Session, paper_id: str, type: str, window: timedelta) -> float:
    """First derivative over the window: (last - first) / span in days; 0 if under two points."""
    points = series(session, paper_id, type, datetime.now(UTC) - window)
    if len(points) < 2:
        return 0.0
    (first_at, first_value), (last_at, last_value) = points[0], points[-1]
    span_days = (last_at - first_at).total_seconds() / 86400.0
    if span_days <= 0:
        return 0.0
    return (last_value - first_value) / span_days
