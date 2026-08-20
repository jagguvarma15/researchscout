"""Unit tests for the refresh scheduler's mechanics (no DB, no network)."""

from contextlib import nullcontext
from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from researchscout.config import Settings
from researchscout.scheduler import Scheduler, Task, build_tasks

NY = ZoneInfo("America/New_York")


def test_task_due_compares_against_deadline() -> None:
    wall = datetime(2026, 8, 4, 12, 0, tzinfo=NY)
    task = Task("t", 10.0, lambda: None, next_at=100.0)
    assert not task.due(99.0, wall)
    assert task.due(100.0, wall)
    assert task.due(101.0, wall)


def test_run_pass_runs_every_task_in_order() -> None:
    calls: list[str] = []
    tasks = [
        Task("a", 1.0, lambda: calls.append("a")),
        Task("b", 1.0, lambda: calls.append("b")),
    ]
    ran = Scheduler(tasks, clock=lambda: 0.0).run_pass()
    assert ran == ["a", "b"]
    assert calls == ["a", "b"]


def test_failing_task_does_not_stop_the_others() -> None:
    calls: list[str] = []

    def boom() -> None:
        raise RuntimeError("nope")

    tasks = [
        Task("bad", 1.0, boom),
        Task("good", 1.0, lambda: calls.append("good")),
    ]
    ran = Scheduler(tasks, clock=lambda: 0.0).run_pass()
    assert ran == ["bad", "good"]
    assert calls == ["good"]


def test_run_due_respects_intervals() -> None:
    calls: list[str] = []
    now = {"t": 1000.0}
    task = Task("x", 60.0, lambda: calls.append("x"))
    sched = Scheduler([task], clock=lambda: now["t"])

    assert sched.run_due(now["t"]) == ["x"]  # cold start: next_at 0 -> due
    assert sched.run_due(now["t"]) == []  # just ran, rescheduled ~1060
    now["t"] += 61.0
    assert sched.run_due(now["t"]) == ["x"]  # interval elapsed
    assert calls == ["x", "x"]


def test_run_forever_stops_when_asked() -> None:
    calls: list[str] = []
    ticks = {"n": 0}

    def stop() -> bool:
        ticks["n"] += 1
        return ticks["n"] > 5

    sched = Scheduler(
        [Task("x", 0.0, lambda: calls.append("x"))],
        tick_sec=0.0,
        clock=lambda: 0.0,
        sleep=lambda _: None,
    )
    sched.run_forever(stop)
    assert calls  # ran at least once before the stop signal


def test_build_tasks_maps_settings_intervals() -> None:
    settings = Settings(
        scheduler_catalog_interval_sec=333,
        scheduler_digest_interval_sec=444,
        scheduler_topics_interval_sec=555,
        scheduler_report_interval_sec=666,
        scheduler_health_interval_sec=777,
    )
    tasks = build_tasks(settings)
    assert [t.name for t in tasks] == ["catalog", "digest", "topics", "report", "health"]
    assert [t.interval_sec for t in tasks] == [333, 444, 555, 666, 777]


def test_tasks_are_on_intervals_unless_times_are_configured() -> None:
    # The default has to be exactly the old behaviour, so a local checkout notices nothing.
    for task in build_tasks(Settings()):
        assert task.at == ()


def test_configured_times_move_the_named_tasks_onto_the_clock() -> None:
    settings = Settings(
        scheduler_batch_pipeline=True,
        scheduler_pipeline_at="05:00,10:00,14:00,17:00",
        scheduler_daily_at="17:00",
        scheduler_timezone="America/New_York",
    )
    by_name = {task.name: task for task in build_tasks(settings)}
    assert by_name["ingest"].at == (time(5, 0), time(10, 0), time(14, 0), time(17, 0))
    # Unset, the signal and citation groups follow the pipeline times.
    assert by_name["signals"].at == by_name["ingest"].at
    assert by_name["citations"].at == by_name["ingest"].at
    assert by_name["catalog"].at == (time(17, 0),)
    assert by_name["report"].at == (time(17, 0),)  # unset report times stay with the daily set
    assert by_name["ingest"].zone.key == "America/New_York"
    assert by_name["health"].at == ()  # the health task stays on its interval everywhere


def test_signal_and_citation_groups_take_their_own_times() -> None:
    settings = Settings(
        scheduler_batch_pipeline=True,
        scheduler_pipeline_at="00:30",
        scheduler_signals_at="08:00,18:00",
        scheduler_citations_at="06:00",
        scheduler_daily_at="17:00",
        scheduler_report_at="07:00",
    )
    by_name = {task.name: task for task in build_tasks(settings)}
    assert by_name["ingest"].at == (time(0, 30),)
    assert by_name["signals"].at == (time(8, 0), time(18, 0))
    assert by_name["citations"].at == (time(6, 0),)
    assert by_name["report"].at == (time(7, 0),)


def test_the_report_can_run_on_its_own_clock() -> None:
    # It describes the day's announcement, so the deployment runs it after the evening fetch
    # while the rest of the daily set keeps its afternoon slot.
    settings = Settings(
        scheduler_batch_pipeline=True,
        scheduler_daily_at="17:00",
        scheduler_report_at="21:00",
    )
    by_name = {task.name: task for task in build_tasks(settings)}
    assert by_name["report"].at == (time(21, 0),)
    assert by_name["digest"].at == (time(17, 0),)
    assert by_name["catalog"].at == (time(17, 0),)


def test_a_wall_clock_task_waits_for_its_next_slot() -> None:
    # Starting at 15:02 must not fire the 14:00 run: on a restart loop that would mean a fetch
    # every time the process came up.
    calls: list[str] = []
    task = Task(
        "x",
        60.0,
        lambda: calls.append("x"),
        at=(time(14, 0), time(17, 0)),
        zone=NY,
    )
    now = {"wall": datetime(2026, 8, 4, 15, 2, tzinfo=NY)}
    sched = Scheduler([task], clock=lambda: 0.0, wall=lambda: now["wall"])

    assert task.next_wall == datetime(2026, 8, 4, 17, 0, tzinfo=NY)
    assert sched.run_due(0.0) == []  # not due: the next slot is 17:00
    assert calls == []
    now["wall"] = datetime(2026, 8, 4, 17, 0, tzinfo=NY)
    assert sched.run_due(0.0) == ["x"]
    assert calls == ["x"]


def test_a_wall_clock_task_reschedules_onto_the_following_slot() -> None:
    now = {"wall": datetime(2026, 8, 4, 13, 59, tzinfo=NY)}
    task = Task("x", 60.0, lambda: None, at=(time(14, 0), time(17, 0)), zone=NY)
    sched = Scheduler([task], clock=lambda: 0.0, wall=lambda: now["wall"])

    assert task.next_wall == datetime(2026, 8, 4, 14, 0, tzinfo=NY)
    now["wall"] = datetime(2026, 8, 4, 14, 0, tzinfo=NY)
    assert sched.run_due(0.0) == ["x"]
    # Having just run at 14:00, the next deadline is 17:00 rather than 14:00 again.
    assert task.next_wall == datetime(2026, 8, 4, 17, 0, tzinfo=NY)


def test_a_slot_slept_over_fires_once_on_wake() -> None:
    # The host slept from before 05:00 until 10:38, freezing the monotonic clock; on wake the
    # 05:00 deadline is in the past. Exactly one catch-up run, then onto the 14:00 slot -
    # judged by monotonic time instead, the deadline would still be hours away.
    calls: list[str] = []
    slots = (time(5, 0), time(10, 0), time(14, 0), time(17, 0))
    task = Task("x", 60.0, lambda: calls.append("x"), at=slots, zone=NY)
    now = {"wall": datetime(2026, 8, 6, 4, 0, tzinfo=NY)}
    sched = Scheduler([task], clock=lambda: 0.0, wall=lambda: now["wall"])

    assert task.next_wall == datetime(2026, 8, 6, 5, 0, tzinfo=NY)
    now["wall"] = datetime(2026, 8, 6, 10, 38, tzinfo=NY)  # asleep through 05:00 and 10:00
    assert sched.run_due(0.0) == ["x"]  # the monotonic clock never moved
    assert calls == ["x"]
    assert task.next_wall == datetime(2026, 8, 6, 14, 0, tzinfo=NY)
    assert sched.run_due(0.0) == []  # one catch-up covers the backlog, not one per slot


def test_a_failed_wall_clock_run_retries_after_the_delay() -> None:
    outcomes = {"fail": True}

    def run() -> None:
        if outcomes["fail"]:
            raise RuntimeError("upstream down")

    task = Task("x", 60.0, run, at=(time(14, 0), time(17, 0)), zone=NY)
    now = {"wall": datetime(2026, 8, 4, 13, 59, tzinfo=NY)}
    sched = Scheduler([task], clock=lambda: 0.0, wall=lambda: now["wall"])

    now["wall"] = datetime(2026, 8, 4, 14, 0, tzinfo=NY)
    assert sched.run_due(0.0) == ["x"]
    # Failed: re-armed half an hour out instead of conceding the day to 17:00.
    assert task.next_wall == datetime(2026, 8, 4, 14, 30, tzinfo=NY)
    assert task.retries_left == task.max_retries - 1

    outcomes["fail"] = False
    now["wall"] = datetime(2026, 8, 4, 14, 30, tzinfo=NY)
    assert sched.run_due(0.0) == ["x"]
    # Succeeded: onto the real slot, with the retry budget reset for it.
    assert task.next_wall == datetime(2026, 8, 4, 17, 0, tzinfo=NY)
    assert task.retries_left == task.max_retries


def test_the_retry_never_passes_the_next_slot() -> None:
    def run() -> None:
        raise RuntimeError("still down")

    task = Task("x", 60.0, run, at=(time(14, 0), time(15, 0)), zone=NY, retry_delay_sec=7200.0)
    now = {"wall": datetime(2026, 8, 4, 13, 59, tzinfo=NY)}
    sched = Scheduler([task], clock=lambda: 0.0, wall=lambda: now["wall"])

    now["wall"] = datetime(2026, 8, 4, 14, 0, tzinfo=NY)
    assert sched.run_due(0.0) == ["x"]
    # A two-hour delay would land at 16:00, past the 15:00 slot: capped at the slot, so
    # a retry can only ever move work earlier than the schedule already would.
    assert task.next_wall == datetime(2026, 8, 4, 15, 0, tzinfo=NY)


def test_retries_exhaust_onto_the_following_slot() -> None:
    def run() -> None:
        raise RuntimeError("still down")

    task = Task("x", 60.0, run, at=(time(14, 0), time(17, 0)), zone=NY, max_retries=1)
    now = {"wall": datetime(2026, 8, 4, 13, 59, tzinfo=NY)}
    sched = Scheduler([task], clock=lambda: 0.0, wall=lambda: now["wall"])

    now["wall"] = datetime(2026, 8, 4, 14, 0, tzinfo=NY)
    assert sched.run_due(0.0) == ["x"]
    assert task.next_wall == datetime(2026, 8, 4, 14, 30, tzinfo=NY)

    now["wall"] = datetime(2026, 8, 4, 14, 30, tzinfo=NY)
    assert sched.run_due(0.0) == ["x"]
    # Budget spent: the next deadline is the real slot, and the budget resets with it.
    assert task.next_wall == datetime(2026, 8, 4, 17, 0, tzinfo=NY)
    assert task.retries_left == 1


def test_an_interval_task_still_starts_due() -> None:
    task = Task("x", 60.0, lambda: None)
    Scheduler([task], clock=lambda: 1000.0, wall=lambda: datetime(2026, 8, 4, 15, 2, tzinfo=NY))
    assert task.next_at == 0.0  # untouched: a fresh process does interval work at once


def test_run_pass_ignores_the_clock_entirely() -> None:
    # --once is a cron entry point: it runs everything whatever the schedule says.
    calls: list[str] = []
    task = Task("x", 60.0, lambda: calls.append("x"), at=(time(17, 0),), zone=NY)
    sched = Scheduler(
        [task], clock=lambda: 0.0, wall=lambda: datetime(2026, 8, 4, 15, 2, tzinfo=NY)
    )
    assert sched.run_pass() == ["x"]
    assert calls == ["x"]


def test_the_stream_owns_the_pipeline_by_default() -> None:
    """Adding these where the stream already runs means two processes on one address."""
    assert "ingest" not in [t.name for t in build_tasks(Settings())]


def test_batch_pipeline_adds_the_work_the_stream_would_do() -> None:
    """An install without the stream would otherwise never see another paper."""
    settings = Settings(
        scheduler_batch_pipeline=True,
        scheduler_ingest_interval_sec=11,
        scheduler_categorize_interval_sec=15,
        scheduler_index_interval_sec=22,
        scheduler_fulltext_interval_sec=33,
        scheduler_signals_interval_sec=44,
    )
    tasks = build_tasks(settings)
    assert [t.name for t in tasks] == [
        "ingest",
        "categorize",
        "index",
        "fulltext",
        "signals",
        "citations",
        "catalog",
        "digest",
        "topics",
        "report",
        "health",
    ]
    assert [t.interval_sec for t in tasks[:5]] == [11, 15, 22, 33, 44]


def test_the_revisions_sweep_needs_its_own_slot() -> None:
    """Unset means not scheduled at all - an interval default would re-walk hourly."""
    on = Settings(scheduler_batch_pipeline=True, scheduler_revisions_at="01:30")
    assert "revisions" in [t.name for t in build_tasks(on)]
    off = Settings(scheduler_batch_pipeline=True)
    assert "revisions" not in [t.name for t in build_tasks(off)]


def _patch_source_run(
    monkeypatch: pytest.MonkeyPatch, sources: list[object], fake_run_ingest: object
) -> None:
    from datetime import UTC, datetime

    monkeypatch.setattr("researchscout.sources.enabled_sources", lambda kind=None: sources)
    monkeypatch.setattr("researchscout.ingest.pipeline.run_ingest", fake_run_ingest)
    monkeypatch.setattr(
        "researchscout.ingest.pipeline.window_start",
        lambda session, name, **kwargs: datetime(2026, 8, 1, tzinfo=UTC),
    )
    monkeypatch.setattr("researchscout.store.db.session_scope", lambda: nullcontext(None))


class _Source:
    def __init__(self, name: str) -> None:
        self.name = name


def test_one_failing_source_does_not_stop_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rate-limited upstream is normal; it must not cost the others their turn — but the
    aggregate still raises so the ledger row names the failure."""
    import httpx

    import researchscout.scheduler as scheduler_mod

    seen: list[str] = []

    def fake_run_ingest(session: object, source: object, since: object, **kwargs: object) -> object:
        name = getattr(source, "name", "?")
        seen.append(name)
        if name == "broken":
            raise httpx.HTTPError("429 from upstream")
        return SimpleNamespace(
            source=name,
            fetched=1,
            new_papers=1,
            signals=0,
            skipped=0,
            stopped_early=None,
            stopped_by_error=False,
        )

    _patch_source_run(monkeypatch, [_Source("broken"), _Source("fine")], fake_run_ingest)

    with pytest.raises(RuntimeError) as excinfo:
        scheduler_mod._ingest(Settings())
    assert seen == ["broken", "fine"]
    # The note names the broken source and still carries the healthy one's outcome.
    assert "broken: failed" in str(excinfo.value)
    assert "fine: fetched=1" in str(excinfo.value)


def test_an_error_stop_fails_the_ledger_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """A walk that lands a page then rate-limits out must not read as success - the pages
    it committed stand, but repeated truncation quietly thins coverage."""
    import researchscout.scheduler as scheduler_mod

    def fake_run_ingest(session: object, source: object, since: object, **kwargs: object) -> object:
        return SimpleNamespace(
            source="arxiv",
            fetched=100,
            new_papers=93,
            signals=0,
            skipped=0,
            stopped_early="429 from upstream",
            stopped_by_error=True,
        )

    _patch_source_run(monkeypatch, [_Source("arxiv")], fake_run_ingest)

    with pytest.raises(RuntimeError) as excinfo:
        scheduler_mod._ingest(Settings())
    # The note still carries what landed before the stop.
    assert "fetched=100" in str(excinfo.value)
    assert "stopped early: 429" in str(excinfo.value)


def test_a_non_http_failure_is_isolated_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """A parse or database error in one source must not cost the sources after it."""
    import researchscout.scheduler as scheduler_mod

    seen: list[str] = []

    def fake_run_ingest(session: object, source: object, since: object, **kwargs: object) -> object:
        name = getattr(source, "name", "?")
        seen.append(name)
        if name == "broken":
            raise ValueError("malformed payload")
        return SimpleNamespace(
            source=name,
            fetched=0,
            new_papers=0,
            signals=3,
            skipped=0,
            stopped_early=None,
            stopped_by_error=False,
        )

    _patch_source_run(monkeypatch, [_Source("broken"), _Source("fine")], fake_run_ingest)

    with pytest.raises(RuntimeError):
        scheduler_mod._signals(Settings())
    assert seen == ["broken", "fine"]


def test_signals_excludes_the_citation_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """Citations belong to the walker task; the fast poll must not double-fetch them."""
    import researchscout.scheduler as scheduler_mod

    seen: list[str] = []

    def fake_run_ingest(session: object, source: object, since: object, **kwargs: object) -> object:
        seen.append(getattr(source, "name", "?"))
        return SimpleNamespace(
            source="x",
            fetched=0,
            new_papers=0,
            signals=0,
            skipped=0,
            stopped_early=None,
            stopped_by_error=False,
        )

    sources = [_Source("semantic_scholar"), _Source("hf_trending"), _Source("openalex")]
    _patch_source_run(monkeypatch, sources, fake_run_ingest)

    scheduler_mod._signals(Settings())
    assert seen == ["hf_trending"]


def test_recorded_wrapper_writes_the_ledger_in_two_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row opens before the task runs and completes after — a hang leaves the open row."""
    import researchscout.scheduler as scheduler_mod

    events: list[tuple[str, object]] = []

    def fake_started(session: object, task: str, *, started_at: object) -> int:
        events.append(("open", task))
        return 7

    def fake_finished(
        session: object, run_id: int, *, finished_at: object, ok: bool, note: str = ""
    ) -> None:
        events.append(("finish", (run_id, ok, note)))

    monkeypatch.setattr("researchscout.store.runs.record_task_started", fake_started)
    monkeypatch.setattr("researchscout.store.runs.record_task_finished", fake_finished)
    monkeypatch.setattr("researchscout.store.db.session_scope", lambda: nullcontext(None))

    scheduler_mod._recorded("fine", lambda: "did the thing")()

    def boom() -> str:
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        scheduler_mod._recorded("broken", boom)()

    assert events == [
        ("open", "fine"),
        ("finish", (7, True, "did the thing")),
        ("open", "broken"),
        ("finish", (7, False, "no")),
    ]


def test_recorded_falls_back_to_a_single_write_when_the_open_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bookkeeping trouble must not stop the task, and the run still lands in the ledger."""
    import researchscout.scheduler as scheduler_mod

    entries: list[tuple[str, bool, str]] = []

    def fake_record(name: str, started: object, *, ok: bool, note: str = "") -> None:
        entries.append((name, ok, note))

    def broken_started(session: object, task: str, *, started_at: object) -> int:
        raise RuntimeError("ledger down")

    monkeypatch.setattr("researchscout.store.runs.record_task_started", broken_started)
    monkeypatch.setattr("researchscout.store.db.session_scope", lambda: nullcontext(None))
    monkeypatch.setattr(scheduler_mod, "_record_safely", fake_record)

    scheduler_mod._recorded("fine", lambda: "note")()
    assert entries == [("fine", True, "note")]


def test_the_heartbeat_ticks_around_tasks() -> None:
    """The healthcheck file must move even while a slot runs several tasks back to back."""
    beats: list[int] = []
    tasks = [Task("a", 1.0, lambda: None), Task("b", 1.0, lambda: None)]
    sched = Scheduler(tasks, clock=lambda: 0.0, heartbeat=lambda: beats.append(1))
    sched.run_due(0.0)
    # One beat after each task plus one for the tick itself.
    assert len(beats) == 3


def test_record_started_writes_the_scheduler_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """The start-up row is what lets deploy-verify tell a young ledger from a stalled loop."""
    import researchscout.scheduler as scheduler_mod

    entries: list[tuple[str, bool, str]] = []

    def fake_record(name: str, started: object, *, ok: bool, note: str = "") -> None:
        entries.append((name, ok, note))

    monkeypatch.setattr(scheduler_mod, "_record_safely", fake_record)
    scheduler_mod.record_started(8)
    assert entries == [("scheduler", True, "started: 8 task(s)")]


def test_the_start_up_summary_names_every_task_and_its_next_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A scheduler that says nothing on start-up hides the case where it schedules no work."""
    tasks = [
        Task("ingest", 60.0, lambda: None, at=(time(5, 0), time(17, 0)), zone=NY),
        Task("digest", 3600.0, lambda: None),
    ]
    with caplog.at_level("INFO", logger="researchscout.scheduler"):
        Scheduler(tasks, clock=lambda: 0.0, wall=lambda: datetime(2026, 8, 4, 15, 2, tzinfo=NY))

    summary = "\n".join(caplog.messages)
    assert "2 task(s)" in summary
    # The wall-clock task reports its times, its zone and the slot it will actually take.
    assert "ingest at 05:00, 17:00 America/New_York, next 2026-08-04 17:00" in summary
    # The interval task says so rather than reporting a time it does not have.
    assert "digest every 3600s, first run now" in summary


def test_a_scheduler_with_nothing_fetching_papers_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The deployment sat for weeks looking healthy with a corpus that never moved."""
    with caplog.at_level("WARNING", logger="researchscout.scheduler"):
        build_tasks(Settings(scheduler_batch_pipeline=False))
    assert "no fetch tasks scheduled" in "\n".join(caplog.messages)

    caplog.clear()
    with caplog.at_level("WARNING", logger="researchscout.scheduler"):
        build_tasks(Settings(scheduler_batch_pipeline=True))
    assert caplog.messages == []
