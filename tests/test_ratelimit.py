import pytest
from fastapi import HTTPException

import researchscout.api.ratelimit as ratelimit_mod
from researchscout.api.ratelimit import check_rate_limit


@pytest.fixture(autouse=True)
def _fresh_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ratelimit_mod, "_windows", {})
    # The sweep is on a timer, and these tests move the clock; without resetting it a test
    # would inherit whether the previous one had just swept.
    monkeypatch.setattr(ratelimit_mod, "_last_sweep", 0)


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


def test_closed_windows_are_swept_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """The map is keyed by client address, so on a public hostname it grows without this."""
    _at(monkeypatch, 1000)
    for key in ("a", "b", "c"):
        check_rate_limit(key, limit=3, window_seconds=60)
    assert set(ratelimit_mod._windows) == {"a", "b", "c"}

    # A later window: only the key being checked is still live, and the sweep runs on the
    # same call rather than needing one of its own.
    _at(monkeypatch, 5000)
    check_rate_limit("d", limit=3, window_seconds=60)
    assert set(ratelimit_mod._windows) == {"d"}


def test_a_live_window_survives_its_own_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The counter is written before the sweep, so a key can never evict itself."""
    _at(monkeypatch, 1000)
    check_rate_limit("u", limit=2, window_seconds=60)
    _at(monkeypatch, 1061)
    check_rate_limit("u", limit=2, window_seconds=60)
    check_rate_limit("u", limit=2, window_seconds=60)
    with pytest.raises(HTTPException):
        check_rate_limit("u", limit=2, window_seconds=60)
