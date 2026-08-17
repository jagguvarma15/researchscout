"""Deployment truth: what is running, how fresh the corpus is, which runs happened.

A public read like /sources: nothing here is about the caller and nothing is secret — the
build SHA names a public commit and the ledger names outcomes. It exists so "is production
current and fetching?" is one request instead of an afternoon of docker inspect; ``make
deploy-verify``, the web footer's freshness line, and the about page's status section all
read it.

The self-checks reported here are database-only: this endpoint renders on page loads and
must never resolve DNS or call out. The network verdicts (the funnel check) arrive via the
scheduler's last ``health`` run, which did the calling on its own time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from researchscout import __version__
from researchscout.api.deps import get_session
from researchscout.api.schemas import HealthCheckInfo, ScheduleGroup, SchedulerRun, SystemStatus
from researchscout.config import get_settings
from researchscout.health import run_health_checks
from researchscout.schedule import next_run, parse_times, previous_run
from researchscout.store.models import PaperRow, SchedulerRunRow
from researchscout.store.runs import last_started, recent_runs

router = APIRouter(tags=["system"])


def _pipeline_due_at() -> datetime | None:
    """The pipeline slot most recently due, from this process's own schedule settings.

    The API and the scheduler share one environment, so the API can say when a run should
    have happened even though the scheduler is the one that runs it — which is what lets
    ``deploy/verify.sh`` name a missing slot instead of shrugging at an empty ledger. None
    when the deployment runs on intervals rather than wall-clock times.
    """
    settings = get_settings()
    times = parse_times(settings.scheduler_pipeline_at)
    if not times:
        return None
    zone = ZoneInfo(settings.scheduler_timezone)
    return previous_run(times, datetime.now(UTC), zone)


def _schedule_groups() -> list[ScheduleGroup]:
    """Every wall-clock group with its configured times and next occurrence."""
    settings = get_settings()
    zone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(UTC)
    raw = {
        "pipeline": settings.scheduler_pipeline_at,
        "signals": settings.scheduler_signals_at,
        "citations": settings.scheduler_citations_at,
        "daily": settings.scheduler_daily_at,
        "report": settings.scheduler_report_at,
    }
    groups: list[ScheduleGroup] = []
    for name, value in raw.items():
        times = parse_times(value)
        if not times:
            continue
        groups.append(
            ScheduleGroup(
                group=name,
                at=[at.strftime("%H:%M") for at in times],
                timezone=settings.scheduler_timezone,
                next_run=next_run(times, now, zone),
            )
        )
    return groups


def _last_health_run(session: Session) -> SchedulerRun | None:
    row = session.execute(
        select(SchedulerRunRow)
        .where(SchedulerRunRow.task == "health", SchedulerRunRow.finished_at.is_not(None))
        .order_by(SchedulerRunRow.finished_at.desc(), SchedulerRunRow.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return SchedulerRun(
        task=row.task,
        started_at=row.started_at,
        finished_at=row.finished_at,
        ok=row.ok,
        note=row.note,
    )


@router.get("/system/status")
def system_status(session: Annotated[Session, Depends(get_session)]) -> SystemStatus:
    settings = get_settings()
    newest_published = session.execute(
        select(func.max(PaperRow.published_at))
    ).scalar_one_or_none()
    newest_created = session.execute(select(func.max(PaperRow.created_at))).scalar_one_or_none()
    papers = session.execute(select(func.count()).select_from(PaperRow)).scalar_one()
    # Read the migration stamp directly: alembic's own table is the one source of truth for
    # what schema this database actually carries, whatever the code beside it expects.
    migration = session.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one_or_none()
    runs = [
        SchedulerRun(
            task=row.task,
            started_at=row.started_at,
            finished_at=row.finished_at,
            ok=row.ok,
            note=row.note,
        )
        for row in recent_runs(session, limit=20)
    ]
    health = [
        HealthCheckInfo(name=check.name, status=check.status, detail=check.detail)
        for check in run_health_checks(session, settings, include_network=False)
    ]
    return SystemStatus(
        version=__version__,
        build_sha=settings.build_sha or None,
        migration=migration,
        papers=papers,
        newest_paper_at=newest_published,
        newest_paper_created_at=newest_created,
        runs=runs,
        pipeline_due_at=_pipeline_due_at(),
        scheduler_started_at=last_started(session),
        health=health,
        last_health_run=_last_health_run(session),
        schedule=_schedule_groups(),
    )
