"""Unit tests for the refresh scheduler's mechanics (no DB, no network)."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

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
        scheduler_digest_interval_sec=444,
        scheduler_topics_interval_sec=555,
        scheduler_report_interval_sec=666,
    )
    tasks = build_tasks(settings)
    assert [t.name for t in tasks] == ["digest", "topics", "report"]
    assert [t.interval_sec for t in tasks] == [444, 555, 666]


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
        return SimpleNamespace(source=name, fetched=1, new_papers=1, signals=0)

    monkeypatch.setattr(
        "researchscout.sources.enabled_sources",
        lambda kind=None: [_Source("broken"), _Source("fine")],
    )
    monkeypatch.setattr("researchscout.ingest.pipeline.run_ingest", fake_run_ingest)
    monkeypatch.setattr(scheduler_mod, "session_scope", nullcontext, raising=False)
    monkeypatch.setattr("researchscout.store.db.session_scope", lambda: nullcontext(None))

    scheduler_mod._ingest(Settings())
    assert seen == ["broken", "fine"]
