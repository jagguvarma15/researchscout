"""The scheduler's run ledger: append, read newest first, trim on write."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from researchscout.store.runs import (
    last_finished,
    last_ok_finish,
    open_runs_older_than,
    recent_runs,
    record_run,
    record_task_finished,
    record_task_started,
)

pytestmark = pytest.mark.integration


def _at(days_ago: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=days_ago)


def test_record_and_read_newest_first(session: Session) -> None:
    record_run(session, "ingest", started_at=_at(0.2), finished_at=_at(0.19), ok=True)
    record_run(session, "digest", started_at=_at(0.1), finished_at=_at(0.09), ok=False, note="429")

    runs = recent_runs(session)
    assert [run.task for run in runs] == ["digest", "ingest"]
    assert runs[0].ok is False
    assert runs[0].note == "429"


def test_old_rows_are_trimmed_on_write(session: Session) -> None:
    record_run(session, "ingest", started_at=_at(60), finished_at=_at(60), ok=True)
    record_run(session, "ingest", started_at=_at(0), finished_at=_at(0), ok=True)

    runs = recent_runs(session)
    assert len(runs) == 1  # the sixty-day-old row went with the write; a ledger, not a log store


def test_note_is_trimmed_to_fit(session: Session) -> None:
    record_run(session, "ingest", started_at=_at(0), finished_at=_at(0), ok=False, note="e" * 1000)
    assert len(recent_runs(session)[0].note) == 400


def test_two_phase_rows_open_then_complete(session: Session) -> None:
    run_id = record_task_started(session, "ingest", started_at=_at(0.01))
    open_row = recent_runs(session)[0]
    assert open_row.finished_at is None
    assert open_row.note == "running"

    record_task_finished(session, run_id, finished_at=_at(0), ok=True, note="fetched=12")
    done = recent_runs(session)[0]
    assert done.finished_at is not None
    assert done.ok is True
    assert done.note == "fetched=12"


def test_running_rows_sort_before_finished_ones(session: Session) -> None:
    record_run(session, "digest", started_at=_at(0.01), finished_at=_at(0), ok=True)
    record_task_started(session, "fulltext", started_at=_at(0.005))
    assert [run.task for run in recent_runs(session)] == ["fulltext", "digest"]


def test_open_rows_survive_the_trim(session: Session) -> None:
    """A hung row is evidence; age must not delete it before anyone reads it."""
    record_task_started(session, "ingest", started_at=_at(60))
    record_run(session, "ingest", started_at=_at(0), finished_at=_at(0), ok=True)
    assert any(run.finished_at is None for run in recent_runs(session))


def test_open_runs_older_than_finds_the_hung_task(session: Session) -> None:
    record_task_started(session, "topics", started_at=_at(0.5))
    record_task_started(session, "digest", started_at=_at(0.001))
    hung = open_runs_older_than(session, cutoff=_at(0.25))
    assert [run.task for run in hung] == ["topics"]


def test_last_finished_takes_any_outcome_but_never_an_open_row(session: Session) -> None:
    record_run(session, "health", started_at=_at(1), finished_at=_at(1), ok=True, note="all ok")
    record_run(session, "health", started_at=_at(0.5), finished_at=_at(0.5), ok=False, note="bad")
    record_task_started(session, "health", started_at=_at(0.001))

    last = last_finished(session, "health")
    assert last is not None
    assert last.ok is False
    assert last.note == "bad"
    assert last_finished(session, "no-such-task") is None


def test_last_ok_finish_ignores_failures(session: Session) -> None:
    record_run(session, "ingest", started_at=_at(2), finished_at=_at(2), ok=True)
    record_run(session, "ingest", started_at=_at(1), finished_at=_at(1), ok=False)
    finish = last_ok_finish(session, "ingest")
    assert finish is not None
    assert abs((finish - _at(2)).total_seconds()) < 60
