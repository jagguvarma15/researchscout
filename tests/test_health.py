"""The self-check module: the weekend-aware arrival rule, the streaks, the verdicts."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from researchscout.config import Settings
from researchscout.health import (
    check_corpus_freshness,
    check_hung_runs,
    check_pipeline_runs,
    check_task_streaks,
    expected_arrival_slots_since,
    overall_ok,
    run_health_checks,
    summarize,
)
from researchscout.store.runs import record_run, record_task_started

NY = ZoneInfo("America/New_York")
SLOT = time(0, 30)


def _ny(day: int, hour: int, minute: int = 0, month: int = 8) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=NY)


# 2026-08-14 is a Friday; 15 Saturday; 16 Sunday; 17 Monday.


def test_a_weekend_gap_misses_nothing() -> None:
    """Friday's papers arrived; Saturday and Sunday mornings never bring any."""
    newest = _ny(14, 2, 0)  # Friday morning arrival
    now = _ny(16, 23, 0)  # Sunday night
    assert expected_arrival_slots_since(newest, now, NY, SLOT) == 0


def test_a_missed_monday_counts_once() -> None:
    newest = _ny(14, 2, 0)  # Friday morning arrival
    now = _ny(17, 9, 0)  # Monday mid-morning, past the slot + slack
    assert expected_arrival_slots_since(newest, now, NY, SLOT) == 1


def test_two_missed_weekdays_count_twice() -> None:
    newest = _ny(13, 2, 0)  # Thursday morning arrival
    now = _ny(17, 9, 0)  # Friday and Monday mornings both passed empty
    assert expected_arrival_slots_since(newest, now, NY, SLOT) == 2


def test_the_slack_hour_keeps_the_current_morning_out() -> None:
    """At 00:45 the slot has fired but the run may still be walking pages."""
    newest = _ny(14, 2, 0)
    now = _ny(17, 0, 45)
    assert expected_arrival_slots_since(newest, now, NY, SLOT) == 0


def test_papers_landing_inside_the_slack_satisfy_the_slot() -> None:
    """The pipeline lands its papers a minute after the slot; that IS the arrival.

    Counting it as missed made corpus_freshness warn permanently: the newest paper of
    every normal day is created inside the grace hour.
    """
    newest = _ny(17, 0, 34)  # Monday 00:34, one minute after the 00:30 run began
    now = _ny(17, 9, 0)
    assert expected_arrival_slots_since(newest, now, NY, SLOT) == 0


def test_summarize_and_overall_verdict() -> None:
    from researchscout.health import HealthCheck

    checks = [
        HealthCheck("pipeline_runs", "ok", "ingest ok 2.0h ago"),
        HealthCheck("corpus_freshness", "fail", "2 expected arrivals missed"),
        HealthCheck("storage", "skipped", "n/a"),
    ]
    note = summarize(checks)
    assert "pipeline_runs=ok" in note
    assert "corpus_freshness=fail(2 expected arrivals missed)" in note
    assert not overall_ok(checks)
    assert overall_ok([c for c in checks if c.status != "fail"])


@pytest.mark.integration
def test_pipeline_runs_reads_the_ledger(session: Session) -> None:
    settings = Settings(scheduler_pipeline_at="00:30")
    now = datetime.now(UTC)
    assert check_pipeline_runs(session, settings, now).status == "warn"  # nothing yet

    record_run(
        session,
        "ingest",
        started_at=now - timedelta(hours=3),
        finished_at=now - timedelta(hours=3),
        ok=True,
    )
    session.commit()
    assert check_pipeline_runs(session, settings, now).status == "ok"

    assert check_pipeline_runs(session, settings, now + timedelta(hours=30)).status == "fail"


@pytest.mark.integration
def test_task_streaks_fail_on_three_and_warn_on_one(session: Session) -> None:
    now = datetime.now(UTC)
    for offset in (3, 2, 1):
        record_run(
            session,
            "digest",
            started_at=now - timedelta(hours=offset),
            finished_at=now - timedelta(hours=offset),
            ok=False,
            note="llm down",
        )
    record_run(session, "topics", started_at=now, finished_at=now, ok=False)
    record_run(session, "ingest", started_at=now, finished_at=now, ok=True)
    # The health task's own rows echo past verdicts; if they counted, one bad stretch
    # would latch task_streaks into failure forever.
    for offset in (3, 2, 1):
        record_run(
            session,
            "health",
            started_at=now - timedelta(hours=offset),
            finished_at=now - timedelta(hours=offset),
            ok=False,
            note="task_streaks=fail",
        )
    session.commit()

    check = check_task_streaks(session)
    assert check.status == "fail"
    assert "digest" in check.detail
    assert "health" not in check.detail


_QUOTA_NOTE = "Error code: 429 - Rate limit exceeded: free-models-per-day"


@pytest.mark.integration
def test_task_streaks_warn_when_the_whole_streak_is_quota(session: Session) -> None:
    now = datetime.now(UTC)
    for offset in (3, 2, 1):
        record_run(
            session,
            "topics",
            started_at=now - timedelta(hours=offset),
            finished_at=now - timedelta(hours=offset),
            ok=False,
            note=_QUOTA_NOTE,
        )
    session.commit()

    check = check_task_streaks(session)
    assert check.status == "warn"
    assert "quota-limited" in check.detail
    assert "topics" in check.detail


@pytest.mark.integration
def test_a_quota_streak_does_not_mask_a_real_failure(session: Session) -> None:
    now = datetime.now(UTC)
    for offset in (3, 2, 1):
        record_run(
            session,
            "topics",
            started_at=now - timedelta(hours=offset),
            finished_at=now - timedelta(hours=offset),
            ok=False,
            note=_QUOTA_NOTE,
        )
        record_run(
            session,
            "digest",
            started_at=now - timedelta(hours=offset),
            finished_at=now - timedelta(hours=offset),
            ok=False,
            note="llm down",
        )
    session.commit()

    check = check_task_streaks(session)
    assert check.status == "fail"
    assert "digest" in check.detail


@pytest.mark.integration
def test_hung_runs_surface_open_rows(session: Session) -> None:
    now = datetime.now(UTC)
    record_task_started(session, "fulltext", started_at=now - timedelta(hours=8))
    session.commit()
    check = check_hung_runs(session, now)
    assert check.status == "fail"
    assert "fulltext" in check.detail


@pytest.mark.integration
def test_run_health_checks_reports_every_check(session: Session) -> None:
    names = [check.name for check in run_health_checks(session, Settings())]
    assert names == ["pipeline_runs", "task_streaks", "corpus_freshness", "hung_run", "storage"]


@pytest.mark.integration
def test_corpus_freshness_skips_on_an_interval_schedule(session: Session) -> None:
    check = check_corpus_freshness(session, Settings(scheduler_pipeline_at=""), datetime.now(UTC))
    assert check.status == "skipped"
