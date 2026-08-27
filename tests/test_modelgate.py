"""The model-pass gate: the cap holds under threads, and a settings change re-sizes it."""

import threading

import pytest

from researchscout.config import get_settings
from researchscout.modelgate import _slots, model_slot


def test_the_cap_holds_under_concurrent_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RS_EMBED_MAX_CONCURRENCY", "1")
    _slots.cache_clear()

    peak = 0
    inside = 0
    lock = threading.Lock()

    def work() -> None:
        nonlocal peak, inside
        with model_slot():
            with lock:
                inside += 1
                peak = max(peak, inside)
            threading.Event().wait(0.01)
            with lock:
                inside -= 1

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak == 1


def test_a_settings_change_gets_a_fresh_semaphore(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RS_EMBED_MAX_CONCURRENCY", "1")
    _slots.cache_clear()
    with model_slot():
        pass
    first = _slots(1)

    monkeypatch.setenv("RS_EMBED_MAX_CONCURRENCY", "3")
    get_settings.cache_clear()
    with model_slot():
        pass
    assert _slots(3) is not first


def test_a_nonsense_limit_still_yields_one_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RS_EMBED_MAX_CONCURRENCY", "0")
    _slots.cache_clear()
    with model_slot():
        pass
