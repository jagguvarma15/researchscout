"""Append-only implicit feedback events (impressions, clicks, dwell, dismissals).

The training substrate for personalization: negatives and position-bias correction need
impressions, so logging starts long before anything consumes it. Saves are deliberately not
duplicated here — ``saved_papers.saved_at`` already records them. The read side below feeds
the For You profile: papers a reader opened count as weak positives, papers they dismissed
as negatives.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from researchscout.store.models import EventRow, PaperEmbeddingRow, PaperRow, SavedPaperRow

EVENT_KINDS = frozenset({"impression", "click", "dwell", "dismiss", "open_pdf"})

# The event kinds that read as "this paper drew the reader in". Impressions are the
# denominator, not a preference, and dismissals are the explicit negative.
_POSITIVE_KINDS = ("click", "dwell", "open_pdf")


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


def positive_event_vectors(
    session: Session, user_sub: str, model_id: str, *, days: int = 30, limit: int = 50
) -> list[tuple[str, str, datetime, list[float]]]:
    """(paper_id, title, last_engaged_at, embedding) for papers this reader recently engaged.

    One row per paper (its latest positive event), newest engagement first, capped so a
    heavy reader's history cannot balloon the profile clustering. Papers the reader has
    saved are excluded — a save is the stronger statement and already feeds the profile
    through ``saved_vectors``; counting the click behind it would double the same paper.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    saved = select(SavedPaperRow.paper_id).where(SavedPaperRow.user_sub == user_sub)
    latest = (
        select(EventRow.paper_id, func.max(EventRow.occurred_at).label("last_at"))
        .where(
            EventRow.user_sub == user_sub,
            EventRow.event.in_(_POSITIVE_KINDS),
            EventRow.occurred_at >= since,
            EventRow.paper_id.not_in(saved),
        )
        .group_by(EventRow.paper_id)
        .subquery()
    )
    rows = session.execute(
        select(latest.c.paper_id, PaperRow.title, latest.c.last_at, PaperEmbeddingRow.embedding)
        .join(PaperRow, PaperRow.id == latest.c.paper_id)
        .join(
            PaperEmbeddingRow,
            (PaperEmbeddingRow.paper_id == latest.c.paper_id)
            & (PaperEmbeddingRow.model_id == model_id),
        )
        .order_by(latest.c.last_at.desc())
        .limit(limit)
    ).all()
    return [
        (paper_id, title, last_at, list(embedding)) for paper_id, title, last_at, embedding in rows
    ]


def dismissed_event_paper_ids(session: Session, user_sub: str) -> list[str]:
    """Every paper this reader has ever dismissed, per the event log.

    The account_dismissals table is a bounded working set the reader can restore from; the
    event log remembers the dismissal even after that. For You treats the union as "do not
    recommend" — a paper someone waved away is the clearest negative the log holds.
    """
    rows = session.execute(
        select(EventRow.paper_id)
        .where(EventRow.user_sub == user_sub, EventRow.event == "dismiss")
        .distinct()
    ).scalars()
    return list(rows)


def prune_events(session: Session, *, keep_days: int = 180) -> int:
    """Drop events older than the window, except dismissals — For You's long-term memory.

    Impressions dominate the table (one per card per page view) and personalization only reads
    the last 30 days of positives, so nothing it uses is lost. Dismissals are the one signal
    that must never expire: re-recommending a waved-away paper reads as deaf.
    """
    cutoff = datetime.now(UTC) - timedelta(days=keep_days)
    result = session.execute(
        delete(EventRow).where(EventRow.occurred_at < cutoff, EventRow.event != "dismiss")
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
