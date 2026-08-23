"""The scheduler's run ledger — which task ran, when, and how it went.

Written around every scheduled task and read by /v1/system/status and ``make deploy-verify``.
This is the difference between "the morning slot ran and found nothing new" and "the morning
slot never ran" — a distinction the container log can also make, but only for whoever goes
and reads it.

Rows are two-phase: written at task start (``finished_at`` NULL, note "running") and
completed on finish, so a task that hangs or dies mid-run leaves a visible open row instead
of nothing at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from researchscout.store.models import SchedulerRunRow

# How much history stays. Enough for a month of review; this is a ledger, not a log store.
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
    """Append one completed run in a single write (the CLI/one-shot path)."""
    session.add(
        SchedulerRunRow(
            task=task, started_at=started_at, finished_at=finished_at, ok=ok, note=note[:400]
        )
    )
    _prune(session)


def record_task_started(session: Session, task: str, *, started_at: datetime) -> int:
    """Open a run row before the task executes; return its id for the finish update."""
    row = SchedulerRunRow(task=task, started_at=started_at, ok=False, note="running")
    session.add(row)
    session.flush()
    _prune(session)
    return row.id


def record_task_finished(
    session: Session, run_id: int, *, finished_at: datetime, ok: bool, note: str = ""
) -> None:
    """Complete a previously opened run row."""
    session.execute(
        update(SchedulerRunRow)
        .where(SchedulerRunRow.id == run_id)
        .values(finished_at=finished_at, ok=ok, note=note[:400])
    )


def _prune(session: Session) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=_KEEP_DAYS)
    session.execute(
        delete(SchedulerRunRow).where(
            SchedulerRunRow.finished_at.is_not(None), SchedulerRunRow.finished_at < cutoff
        )
    )


def recent_runs(session: Session, *, limit: int = 20) -> list[SchedulerRunRow]:
    """The latest runs, still-running rows first, then newest finish first."""
    stmt = (
        select(SchedulerRunRow)
        .order_by(SchedulerRunRow.finished_at.desc().nulls_first(), SchedulerRunRow.id.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())


def open_runs_older_than(session: Session, *, cutoff: datetime) -> list[SchedulerRunRow]:
    """Runs that started before ``cutoff`` and never finished — the hung-task signal."""
    stmt = (
        select(SchedulerRunRow)
        .where(SchedulerRunRow.finished_at.is_(None), SchedulerRunRow.started_at < cutoff)
        .order_by(SchedulerRunRow.started_at)
    )
    return list(session.execute(stmt).scalars())


def last_started(session: Session) -> datetime | None:
    """When the scheduler loop most recently came up, or None if it never has.

    Its own row rather than the recent-runs window because a day of runs scrolls the start-up
    past any fixed limit, and "was the loop already up when that slot arrived" is exactly the
    question a missed-slot check must still be able to answer.
    """
    stmt = (
        select(SchedulerRunRow.started_at)
        .where(SchedulerRunRow.task == "scheduler")
        .order_by(SchedulerRunRow.started_at.desc(), SchedulerRunRow.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def last_ok_finish(session: Session, task: str) -> datetime | None:
    """When ``task`` last finished successfully, or None."""
    stmt = (
        select(SchedulerRunRow.finished_at)
        .where(SchedulerRunRow.task == task, SchedulerRunRow.ok.is_(True))
        .order_by(SchedulerRunRow.finished_at.desc().nulls_last(), SchedulerRunRow.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def last_finished(session: Session, task: str) -> SchedulerRunRow | None:
    """The most recent finished run of ``task`` regardless of outcome, or None.

    The finished filter keeps a caller's own still-open row out — the health task reads
    this mid-run to decide whether the failure it is looking at is news.
    """
    stmt = (
        select(SchedulerRunRow)
        .where(SchedulerRunRow.task == task, SchedulerRunRow.finished_at.is_not(None))
        .order_by(SchedulerRunRow.finished_at.desc(), SchedulerRunRow.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def recent_finished_by_task(
    session: Session, *, per_task: int = 3
) -> dict[str, list[SchedulerRunRow]]:
    """The last ``per_task`` finished runs of every task, newest first (for streak checks)."""
    stmt = select(SchedulerRunRow).where(SchedulerRunRow.finished_at.is_not(None))
    grouped: dict[str, list[SchedulerRunRow]] = {}
    rows = session.execute(
        stmt.order_by(SchedulerRunRow.finished_at.desc(), SchedulerRunRow.id.desc()).limit(400)
    ).scalars()
    for row in rows:
        bucket = grouped.setdefault(row.task, [])
        if len(bucket) < per_task:
            bucket.append(row)
    return grouped
