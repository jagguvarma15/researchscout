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
