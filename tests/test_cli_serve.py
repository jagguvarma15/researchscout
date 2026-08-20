"""The serve all command: one process wires the scheduler thread beside the API server."""

import threading
from typing import Any

import pytest
import uvicorn
from typer.testing import CliRunner

import researchscout.cli as cli
import researchscout.scheduler as scheduler_mod


def test_serve_all_wires_scheduler_thread_and_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}
    ran = threading.Event()

    monkeypatch.setattr(cli, "_warm_models", lambda: calls.__setitem__("warmed", True))

    def fake_build_tasks(settings: Any, heartbeat: Any = None) -> list[str]:
        calls["build_settings"] = settings
        return ["ingest", "index", "health"]

    class FakeScheduler:
        def __init__(self, tasks: Any, tick_sec: int, heartbeat: Any = None) -> None:
            calls["tasks"] = tasks
            calls["tick_sec"] = tick_sec

        def run_forever(self, stop: Any) -> None:
            calls["thread_name"] = threading.current_thread().name
            ran.set()

    monkeypatch.setattr(scheduler_mod, "build_tasks", fake_build_tasks)
    monkeypatch.setattr(scheduler_mod, "Scheduler", FakeScheduler)
    monkeypatch.setattr(
        scheduler_mod, "record_started", lambda count: calls.__setitem__("recorded", count)
    )
    monkeypatch.setattr(
        uvicorn, "run", lambda app, host, port: calls.__setitem__("uvicorn", (app, host, port))
    )

    result = CliRunner().invoke(cli.app, ["serve", "all", "--host", "0.0.0.0", "--port", "8123"])
    assert result.exit_code == 0, result.output
    # The scheduler must run on its own daemon thread, not block the server start-up.
    assert ran.wait(5)
    assert calls["warmed"] is True
    assert calls["tasks"] == ["ingest", "index", "health"]
    assert calls["recorded"] == 3
    assert calls["thread_name"] == "scheduler"
    assert calls["uvicorn"] == ("researchscout.api.main:app", "0.0.0.0", 8123)


def test_a_dying_scheduler_thread_takes_the_process_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead thread behind a healthy /healthz would freeze the corpus invisibly; the
    exit is what turns it into a restart the platform performs and records."""
    calls: dict[str, Any] = {}
    exited = threading.Event()

    monkeypatch.setattr(cli, "_warm_models", lambda: None)
    monkeypatch.setattr(scheduler_mod, "build_tasks", lambda settings, heartbeat=None: [])
    monkeypatch.setattr(scheduler_mod, "record_started", lambda count: None)
    monkeypatch.setattr(
        scheduler_mod, "record_crashed", lambda note: calls.__setitem__("crash_note", note)
    )

    class DyingScheduler:
        def __init__(self, tasks: Any, tick_sec: int, heartbeat: Any = None) -> None:
            pass

        def run_forever(self, stop: Any) -> None:
            raise RuntimeError("the loop broke")

    monkeypatch.setattr(scheduler_mod, "Scheduler", DyingScheduler)

    import os as os_mod

    def fake_exit(code: int) -> None:
        calls["exit_code"] = code
        exited.set()

    monkeypatch.setattr(os_mod, "_exit", fake_exit)
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: exited.wait(5))

    result = CliRunner().invoke(cli.app, ["serve", "all"])
    assert result.exit_code == 0, result.output
    assert exited.wait(5)
    assert calls["exit_code"] == 1
    assert calls["crash_note"] == "thread died: the loop broke"
