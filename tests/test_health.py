"""The self-check module: the weekend-aware arrival rule, the streaks, the funnel check."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy.orm import Session

from researchscout.config import Settings
from researchscout.health import (
    check_corpus_freshness,
    check_funnel_dns,
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


def test_funnel_check_passes_when_the_record_resolves() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["name"] == "site.example.ts.net"
        return httpx.Response(200, json={"Answer": [{"data": "1.2.3.4"}]})

    transport = httpx.MockTransport(handler)
    settings = Settings(public_hostname="site.example.ts.net")
    with _patched_doh(transport):
        check = check_funnel_dns(settings)
    assert check.status == "ok"


def test_funnel_check_fails_on_an_empty_answer() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"Status": 3}))
    settings = Settings(public_hostname="site.example.ts.net")
    with _patched_doh(transport):
        check = check_funnel_dns(settings)
    assert check.status == "fail"
    assert "tailscale funnel" in check.detail


def test_funnel_check_warns_when_the_resolver_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    settings = Settings(public_hostname="site.example.ts.net")
    with _patched_doh(httpx.MockTransport(handler)):
        check = check_funnel_dns(settings)
    assert check.status == "warn"  # network trouble here is not proof the funnel is down


def test_funnel_check_skips_without_a_hostname() -> None:
    assert check_funnel_dns(Settings(public_hostname="")).status == "skipped"


class _patched_doh:
    """Route the module's httpx.get through a MockTransport for the duration."""

    def __init__(self, transport: httpx.MockTransport) -> None:
        self._client = httpx.Client(transport=transport)

    def __enter__(self) -> None:
        self._original = httpx.get
        httpx.get = self._client.get  # type: ignore[assignment]

    def __exit__(self, *args: object) -> None:
        httpx.get = self._original  # type: ignore[assignment]
        self._client.close()


def test_summarize_and_overall_verdict() -> None:
    from researchscout.health import HealthCheck

    checks = [
        HealthCheck("pipeline_runs", "ok", "ingest ok 2.0h ago"),
        HealthCheck("funnel_dns", "fail", "no record"),
        HealthCheck("storage", "skipped", "n/a"),
    ]
    note = summarize(checks)
    assert "pipeline_runs=ok" in note
    assert "funnel_dns=fail(no record)" in note
    assert not overall_ok(checks)
    assert overall_ok([c for c in checks if c.status != "fail"])


@pytest.mark.integration
def test_pipeline_runs_reads_the_ledger(session: Session) -> None:
    settings = Settings(scheduler_pipeline_at="00:30")
    now = datetime.now(UTC)
    assert check_pipeline_runs(session, settings, now).status == "warn"  # nothing yet

    record_run(session, "ingest", started_at=now - timedelta(hours=3),
               finished_at=now - timedelta(hours=3), ok=True)
    session.commit()
    assert check_pipeline_runs(session, settings, now).status == "ok"

    assert check_pipeline_runs(session, settings, now + timedelta(hours=30)).status == "fail"


@pytest.mark.integration
def test_task_streaks_fail_on_three_and_warn_on_one(session: Session) -> None:
    now = datetime.now(UTC)
    for offset in (3, 2, 1):
        record_run(session, "digest", started_at=now - timedelta(hours=offset),
                   finished_at=now - timedelta(hours=offset), ok=False, note="llm down")
    record_run(session, "topics", started_at=now, finished_at=now, ok=False)
    record_run(session, "ingest", started_at=now, finished_at=now, ok=True)
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
def test_run_health_checks_stays_database_only_by_default(session: Session) -> None:
    settings = Settings(public_hostname="site.example.ts.net")
    names = [check.name for check in run_health_checks(session, settings)]
    assert "funnel_dns" not in names  # the endpoint path must never resolve DNS inline
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"Answer": [{"data": "1.2.3.4"}]})
    )
    with _patched_doh(transport):
        with_network = [
            check.name for check in run_health_checks(session, settings, include_network=True)
        ]
    assert "funnel_dns" in with_network


@pytest.mark.integration
def test_corpus_freshness_skips_on_an_interval_schedule(session: Session) -> None:
    check = check_corpus_freshness(session, Settings(scheduler_pipeline_at=""), datetime.now(UTC))
    assert check.status == "skipped"
