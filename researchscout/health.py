"""Self-checks: is the pipeline landing papers and running its tasks on schedule.

The checks are pure functions over the ledger, the corpus, and the settings, so the same list
serves three consumers: the scheduler's health task (which writes the verdict to the ledger),
the status endpoint (it renders on page loads, so every check must stay database-only), and
tests.

The weekend logic lives in the corpus-freshness check and nowhere else: the pipeline RUNS
every day, but arXiv announces Sunday through Thursday evenings only, so new papers are
expected on Monday-through-Friday mornings and a quiet weekend is health, not failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from researchscout.config import Settings
from researchscout.llm.errors import is_quota_note
from researchscout.schedule import parse_times
from researchscout.store.models import PaperRow, RawItemRow, SignalRow
from researchscout.store.runs import last_ok_finish, open_runs_older_than, recent_finished_by_task

Status = Literal["ok", "warn", "fail", "skipped"]

_RUN_SLACK = timedelta(hours=2)
_ARRIVAL_SLACK = timedelta(hours=1)
_HUNG_AFTER = timedelta(hours=6)
_STREAK_LEN = 3
_RAW_ROWS_CEILING = 500_000
_SIGNAL_ROWS_CEILING = 5_000_000


@dataclass(frozen=True)
class HealthCheck:
    name: str
    status: Status
    detail: str


def check_pipeline_runs(session: Session, settings: Settings, now: datetime) -> HealthCheck:
    """Did ingest complete successfully within its expected cadence (flat, weekend-blind)?"""
    if settings.scheduler_pipeline_at:
        expected = timedelta(hours=24) + _RUN_SLACK
    else:
        expected = timedelta(seconds=settings.scheduler_ingest_interval_sec) + _RUN_SLACK
    last = last_ok_finish(session, "ingest")
    if last is None:
        return HealthCheck("pipeline_runs", "warn", "no successful ingest recorded yet")
    age = now - last
    if age > expected:
        hours = age.total_seconds() / 3600
        return HealthCheck("pipeline_runs", "fail", f"last successful ingest {hours:.1f}h ago")
    return HealthCheck("pipeline_runs", "ok", f"ingest ok {age.total_seconds() / 3600:.1f}h ago")


def check_task_streaks(session: Session) -> HealthCheck:
    """Tasks whose recent finishes are all failures fail here; a single miss only warns.

    A streak made entirely of rate-limit failures also only warns: an exhausted quota is a
    known operating state on the free tier, not an incident, and the damage it could hide
    (a stale corpus) has its own failing check in ``check_corpus_freshness``.
    """
    failing: list[str] = []
    degraded: list[str] = []
    warning: list[str] = []
    for task, runs in recent_finished_by_task(session, per_task=_STREAK_LEN).items():
        if task in ("scheduler", "health") or not runs:
            # "scheduler" rows are start markers, and "health" rows echo these very
            # checks — counting either would latch an old verdict into a new failure.
            continue
        if len(runs) >= _STREAK_LEN and all(not run.ok for run in runs):
            entry = f"{task} ({runs[0].note or 'no note'})"
            if all(is_quota_note(run.note or "") for run in runs):
                degraded.append(entry)
            else:
                failing.append(entry)
        elif not runs[0].ok:
            warning.append(task)
    if failing:
        return HealthCheck("task_streaks", "fail", "failing repeatedly: " + "; ".join(failing))
    parts = []
    if degraded:
        parts.append("quota-limited: " + "; ".join(degraded))
    if warning:
        parts.append("last run failed: " + ", ".join(warning))
    if parts:
        return HealthCheck("task_streaks", "warn", "; ".join(parts))
    return HealthCheck("task_streaks", "ok", "no failing streaks")


def expected_arrival_slots_since(
    newest: datetime, now: datetime, zone: ZoneInfo, slot: time
) -> int:
    """How many weekday arrival slots (Mon-Fri in ``zone``) passed with nothing landing.

    Monday through Friday mornings are exactly the mornings after a Sunday-through-Thursday
    arXiv announcement; Saturday and Sunday mornings never bring papers and never count.
    A slot is missed only when no paper has arrived since the slot itself fired AND its
    grace hour has passed - a run that lands papers minutes after the slot satisfies it,
    rather than being disqualified for arriving inside the slack.
    """
    count = 0
    local_now = now.astimezone(zone)
    day = newest.astimezone(zone).date()
    while day <= local_now.date():
        if day.weekday() < 5:
            slot_moment = datetime.combine(day, slot).replace(tzinfo=zone)
            if newest < slot_moment and slot_moment + _ARRIVAL_SLACK <= now:
                count += 1
        day += timedelta(days=1)
    return count


def check_corpus_freshness(session: Session, settings: Settings, now: datetime) -> HealthCheck:
    """Weekend-aware: how many expected announcement mornings have passed with no arrival?"""
    times = parse_times(settings.scheduler_pipeline_at)
    if not times:
        return HealthCheck("corpus_freshness", "skipped", "no wall-clock pipeline configured")
    newest = session.execute(select(func.max(PaperRow.created_at))).scalar_one_or_none()
    if newest is None:
        return HealthCheck("corpus_freshness", "warn", "corpus is empty")
    zone = ZoneInfo(settings.scheduler_timezone)
    missed = expected_arrival_slots_since(newest, now, zone, times[0])
    age_hours = (now - newest).total_seconds() / 3600
    if missed >= 2:
        return HealthCheck(
            "corpus_freshness",
            "fail",
            f"{missed} expected announcements not landed (newest {age_hours:.1f}h old)",
        )
    if missed == 1:
        return HealthCheck(
            "corpus_freshness",
            "warn",
            f"one expected announcement not landed (holiday, or a failed run; "
            f"newest {age_hours:.1f}h old)",
        )
    return HealthCheck("corpus_freshness", "ok", f"newest arrival {age_hours:.1f}h old")


def check_hung_runs(session: Session, now: datetime) -> HealthCheck:
    """Open ledger rows older than the hang threshold name the task that never came back."""
    hung = open_runs_older_than(session, cutoff=now - _HUNG_AFTER)
    if hung:
        names = ", ".join(f"{run.task} (started {run.started_at:%Y-%m-%d %H:%M})" for run in hung)
        return HealthCheck("hung_run", "fail", "never finished: " + names)
    return HealthCheck("hung_run", "ok", "no hung runs")


def check_storage(session: Session, settings: Settings, now: datetime) -> HealthCheck:
    """Is retention holding? Old raw payloads surviving past the window mean the prune died."""
    cutoff = now - timedelta(days=settings.raw_items_keep_days + 3)
    stale = session.execute(
        select(RawItemRow.id).where(RawItemRow.fetched_at < cutoff).limit(1)
    ).scalar_one_or_none()
    raw_rows = session.execute(select(func.count()).select_from(RawItemRow)).scalar_one()
    signal_rows = session.execute(select(func.count()).select_from(SignalRow)).scalar_one()
    detail = f"raw_items={raw_rows} signals={signal_rows}"
    if stale is not None:
        return HealthCheck("storage", "warn", f"raw payloads outliving retention; {detail}")
    if raw_rows > _RAW_ROWS_CEILING or signal_rows > _SIGNAL_ROWS_CEILING:
        return HealthCheck("storage", "warn", f"table growth past expectations; {detail}")
    return HealthCheck("storage", "ok", detail)


def run_health_checks(
    session: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> list[HealthCheck]:
    """Every check, in reporting order. All checks read the database only."""
    now = now or datetime.now(UTC)
    return [
        check_pipeline_runs(session, settings, now),
        check_task_streaks(session),
        check_corpus_freshness(session, settings, now),
        check_hung_runs(session, now),
        check_storage(session, settings, now),
    ]


def summarize(checks: list[HealthCheck]) -> str:
    """The compact one-line verdict the ledger note carries."""
    parts = []
    for check in checks:
        if check.status in ("ok", "skipped"):
            parts.append(f"{check.name}={check.status}")
        else:
            parts.append(f"{check.name}={check.status}({check.detail})")
    return " ".join(parts)


def overall_ok(checks: list[HealthCheck]) -> bool:
    return all(check.status != "fail" for check in checks)
