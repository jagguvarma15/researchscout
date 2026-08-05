"""The scheduler's run ledger — which task ran, when, and how it went.

Written around every scheduled task and read by /v1/system/status, ``make deploy-verify`` and
the Grafana ingest dashboard. This is the difference between "the 05:00 slot ran and found
nothing new" and "the 05:00 slot never ran" — a distinction the container log can also make,
but only for whoever goes and reads it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from researchscout.store.models import SchedulerRunRow

# How much history stays. Enough for a month of dashboards; this is a ledger, not a log store.
_KEEP_DAYS = 45


def record_run(
    session: Session,
    task: str,
    *,
    started_at: datetime,
    finished_at: datetime,
    ok: bool,
    note: str = "",
) -> None:
    """Append one completed run, trimming the note to fit and the old tail away."""
    session.add(
        SchedulerRunRow(
            task=task, started_at=started_at, finished_at=finished_at, ok=ok, note=note[:400]
        )
    )
    cutoff = datetime.now(UTC) - timedelta(days=_KEEP_DAYS)
    session.execute(delete(SchedulerRunRow).where(SchedulerRunRow.finished_at < cutoff))


def recent_runs(session: Session, *, limit: int = 20) -> list[SchedulerRunRow]:
    """The latest completed runs, newest first."""
    stmt = (
        select(SchedulerRunRow)
        .order_by(SchedulerRunRow.finished_at.desc(), SchedulerRunRow.id.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())
