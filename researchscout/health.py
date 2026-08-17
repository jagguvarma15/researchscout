"""Self-checks: is the pipeline landing papers, running its tasks, and reachable in public.

The checks are pure functions over the ledger, the corpus, and the settings, so the same list
serves three consumers: the scheduler's health task (which includes the network checks and
writes the verdict to the ledger), the status endpoint (database-only — it renders on page
loads and must not resolve DNS), and tests.

The weekend logic lives in the corpus-freshness check and nowhere else: the pipeline RUNS
every day, but arXiv announces Sunday through Thursday evenings only, so new papers are
expected on Monday-through-Friday mornings and a quiet weekend is health, not failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from researchscout.config import Settings
from researchscout.schedule import parse_times
from researchscout.store.models import PaperRow, RawItemRow, SignalRow
from researchscout.store.runs import last_ok_finish, open_runs_older_than, recent_finished_by_task

Status = Literal["ok", "warn", "fail", "skipped"]

_DOH_URL = "https://dns.google/resolve"
_DOH_TIMEOUT = 5.0
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
    """Tasks whose recent finishes are all failures fail here; a single miss only warns."""
    failing: list[str] = []
    warning: list[str] = []
    for task, runs in recent_finished_by_task(session, per_task=_STREAK_LEN).items():
        if task == "scheduler" or not runs:
            continue
        if len(runs) >= _STREAK_LEN and all(not run.ok for run in runs):
            failing.append(f"{task} ({runs[0].note or 'no note'})")
        elif not runs[0].ok:
            warning.append(task)
    if failing:
        return HealthCheck("task_streaks", "fail", "failing repeatedly: " + "; ".join(failing))
    if warning:
        return HealthCheck("task_streaks", "warn", "last run failed: " + ", ".join(warning))
    return HealthCheck("task_streaks", "ok", "no failing streaks")


def expected_arrival_slots_since(
    newest: datetime, now: datetime, zone: ZoneInfo, slot: time
) -> int:
    """How many weekday arrival moments (slot + slack, Mon-Fri in ``zone``) fall in
    ``(newest, now]``.

    Monday through Friday mornings are exactly the mornings after a Sunday-through-Thursday
    arXiv announcement; Saturday and Sunday mornings never bring papers and never count.
    """
    count = 0
    local_now = now.astimezone(zone)
    day = newest.astimezone(zone).date()
    while day <= local_now.date():
        if day.weekday() < 5:
            moment = datetime.combine(day, slot).replace(tzinfo=zone) + _ARRIVAL_SLACK
            if newest < moment <= now:
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


def check_funnel_dns(settings: Settings) -> HealthCheck:
    """Resolve the public hostname through a public resolver — the tailnet's answer lies.

    A missing record means the world cannot reach the API even though every local probe
    passes; that is precisely the failure a reboot can cause while the tailscale client
    still reports the funnel as on.
    """
    host = settings.public_hostname
    if not host:
        return HealthCheck("funnel_dns", "skipped", "no public hostname configured")
    try:
        resp = httpx.get(_DOH_URL, params={"name": host, "type": "A"}, timeout=_DOH_TIMEOUT)
        resp.raise_for_status()
        answers = resp.json().get("Answer") or []
    except (httpx.HTTPError, ValueError) as exc:
        return HealthCheck("funnel_dns", "warn", f"resolver unreachable: {exc}")
    if not answers:
        return HealthCheck(
            "funnel_dns",
            "fail",
            f"{host} has no public DNS record - re-assert with: tailscale funnel --bg 8001",
        )
    return HealthCheck("funnel_dns", "ok", f"{host} resolves publicly")


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
    include_network: bool = False,
) -> list[HealthCheck]:
    """Every check, in reporting order; network checks only when asked for."""
    now = now or datetime.now(UTC)
    checks = [
        check_pipeline_runs(session, settings, now),
        check_task_streaks(session),
        check_corpus_freshness(session, settings, now),
        check_hung_runs(session, now),
    ]
    if include_network:
        checks.append(check_funnel_dns(settings))
    checks.append(check_storage(session, settings, now))
    return checks


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
