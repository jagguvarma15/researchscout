"""Unit tests for the refresh scheduler's mechanics (no DB, no network)."""

from researchscout.config import Settings
from researchscout.scheduler import Scheduler, Task, build_tasks


def test_task_due_compares_against_deadline() -> None:
    task = Task("t", 10.0, lambda: None, next_at=100.0)
    assert not task.due(99.0)
    assert task.due(100.0)
    assert task.due(101.0)


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
        scheduler_ingest_interval_sec=111,
        scheduler_index_interval_sec=222,
        scheduler_signals_interval_sec=333,
        scheduler_digest_interval_sec=444,
    )
    tasks = build_tasks(settings)
    assert [t.name for t in tasks] == ["ingest", "index", "signals", "digest"]
    assert [t.interval_sec for t in tasks] == [111, 222, 333, 444]
