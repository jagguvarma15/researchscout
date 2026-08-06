"""Deployment truth: what is running, how fresh the corpus is, which runs happened.

A public read like /sources: nothing here is about the caller and nothing is secret — the
build SHA names a public commit and the ledger names outcomes. It exists so "is production
current and fetching?" is one request instead of an afternoon of docker inspect; ``make
deploy-verify`` and the web footer's freshness line both read it.
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
from researchscout.api.schemas import SchedulerRun, SystemStatus
from researchscout.config import get_settings
from researchscout.schedule import parse_times, previous_run
from researchscout.store.models import PaperRow
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


@router.get("/system/status")
def system_status(session: Annotated[Session, Depends(get_session)]) -> SystemStatus:
    newest = session.execute(select(func.max(PaperRow.published_at))).scalar_one_or_none()
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
    return SystemStatus(
        version=__version__,
        build_sha=get_settings().build_sha or None,
        migration=migration,
        papers=papers,
        newest_paper_at=newest,
        runs=runs,
        pipeline_due_at=_pipeline_due_at(),
        scheduler_started_at=last_started(session),
    )
