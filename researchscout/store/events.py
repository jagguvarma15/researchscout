"""Append-only implicit feedback events (impressions, clicks, dwell, dismissals).

The training substrate for future personalization: negatives and position-bias correction need
impressions, so logging starts long before anything consumes it. Saves are deliberately not
duplicated here — ``saved_papers.saved_at`` already records them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from researchscout.store.models import EventRow, PaperRow

EVENT_KINDS = frozenset({"impression", "click", "dwell", "dismiss", "open_pdf"})


@dataclass(frozen=True)
class EventInput:
    event: str
    paper_id: str
    rank: int | None = None
    value: float | None = None
    surface: str | None = None


def append_events(session: Session, user_sub: str, events: Sequence[EventInput]) -> int:
    """Store a batch of events; unknown paper ids are dropped (beacons are best-effort)."""
    if not events:
        return 0
    known = set(
        session.execute(
            select(PaperRow.id).where(PaperRow.id.in_({event.paper_id for event in events}))
        ).scalars()
    )
    rows = [
        EventRow(
            user_sub=user_sub,
            event=event.event,
            paper_id=event.paper_id,
            rank=event.rank,
            value=event.value,
            surface=event.surface,
        )
        for event in events
        if event.paper_id in known
    ]
    session.add_all(rows)
    session.flush()
    return len(rows)
