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
    )
    tasks = build_tasks(settings)
    assert [t.name for t in tasks] == ["catalog", "digest", "topics", "report"]
    assert [t.interval_sec for t in tasks] == [333, 444, 555, 666]


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
    assert by_name["signals"].at == by_name["ingest"].at
    assert by_name["catalog"].at == (time(17, 0),)
    assert by_name["report"].at == (time(17, 0),)
    assert by_name["ingest"].zone.key == "America/New_York"


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
        scheduler_index_interval_sec=22,
        scheduler_fulltext_interval_sec=33,
        scheduler_signals_interval_sec=44,
    )
    tasks = build_tasks(settings)
    assert [t.name for t in tasks] == [
        "ingest",
        "index",
        "fulltext",
        "signals",
        "catalog",
        "digest",
        "topics",
        "report",
    ]
    assert [t.interval_sec for t in tasks[:4]] == [11, 22, 33, 44]


def test_one_failing_source_does_not_stop_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rate-limited upstream is normal; it must not cost the others their turn."""
    import httpx

    import researchscout.scheduler as scheduler_mod

    class _Source:
        def __init__(self, name: str) -> None:
            self.name = name

    seen: list[str] = []

    def fake_run_ingest(session: object, source: object, since: object, **kwargs: object) -> object:
        name = getattr(source, "name", "?")
        seen.append(name)
        if name == "broken":
            raise httpx.HTTPError("429 from upstream")
        return SimpleNamespace(source=name, fetched=1, new_papers=1, signals=0, stopped_early=None)

    monkeypatch.setattr(
        "researchscout.sources.enabled_sources",
        lambda kind=None: [_Source("broken"), _Source("fine")],
    )
    monkeypatch.setattr("researchscout.ingest.pipeline.run_ingest", fake_run_ingest)
    monkeypatch.setattr(scheduler_mod, "session_scope", nullcontext, raising=False)
    monkeypatch.setattr("researchscout.store.db.session_scope", lambda: nullcontext(None))

    scheduler_mod._ingest(Settings())
    assert seen == ["broken", "fine"]


def test_recorded_wrapper_writes_the_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every scheduled run lands in the ledger — the success, and the failure with its reason."""
    import researchscout.scheduler as scheduler_mod

    entries: list[tuple[str, bool, str]] = []

    def fake_record(name: str, started: object, *, ok: bool, note: str = "") -> None:
        entries.append((name, ok, note))

    monkeypatch.setattr(scheduler_mod, "_record_safely", fake_record)

    scheduler_mod._recorded("fine", lambda: None)()

    def boom() -> None:
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        scheduler_mod._recorded("broken", boom)()

    assert entries == [("fine", True, ""), ("broken", False, "no")]


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
