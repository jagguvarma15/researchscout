"""The scheduler's run ledger: append, read newest first, trim on write."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from researchscout.store.runs import recent_runs, record_run

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
