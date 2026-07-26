import pytest
from fastapi import HTTPException

import researchscout.api.ratelimit as ratelimit_mod
from researchscout.api.ratelimit import check_rate_limit


@pytest.fixture(autouse=True)
def _fresh_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ratelimit_mod, "_windows", {})


def _at(monkeypatch: pytest.MonkeyPatch, now: int) -> None:
    monkeypatch.setattr(ratelimit_mod.time, "time", lambda: now)


def test_under_limit_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _at(monkeypatch, 1000)
    for _ in range(3):
        check_rate_limit("u", limit=3, window_seconds=60)


def test_over_limit_is_429_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    _at(monkeypatch, 1000)
    for _ in range(3):
        check_rate_limit("u", limit=3, window_seconds=60)
    with pytest.raises(HTTPException) as excinfo:
        check_rate_limit("u", limit=3, window_seconds=60)
    assert excinfo.value.status_code == 429
    assert excinfo.value.headers["Retry-After"] == str(60 - (1000 % 60))


def test_new_window_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    _at(monkeypatch, 1000)
    for _ in range(3):
        check_rate_limit("u", limit=3, window_seconds=60)
    _at(monkeypatch, 1060)
    check_rate_limit("u", limit=3, window_seconds=60)


def test_keys_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    _at(monkeypatch, 1000)
    for _ in range(3):
        check_rate_limit("a", limit=3, window_seconds=60)
    check_rate_limit("b", limit=3, window_seconds=60)
