"""The in-process For You profile cache: TTL, per-user invalidation, and the sweep."""

import pytest

from researchscout.retrieve import profile_cache


@pytest.fixture(autouse=True)
def _clean() -> None:
    profile_cache.clear()


def test_put_and_get_roundtrip() -> None:
    profile_cache.put("alice", "bge", 3, "profile-a")
    assert profile_cache.get("alice", "bge", 3) == "profile-a"
    assert profile_cache.get("alice", "bge", 2) is None  # different k is a different key
    assert profile_cache.get("bob", "bge", 3) is None


def test_expiry_drops_the_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(profile_cache.time, "monotonic", lambda: clock["t"])
    profile_cache.put("alice", "bge", 3, "profile-a")
    clock["t"] += profile_cache._TTL_SECONDS - 1
    assert profile_cache.get("alice", "bge", 3) == "profile-a"
    clock["t"] += 2  # now past the TTL
    assert profile_cache.get("alice", "bge", 3) is None


def test_invalidate_drops_only_one_user() -> None:
    profile_cache.put("alice", "bge", 3, "a3")
    profile_cache.put("alice", "bge", 2, "a2")  # same user, different k
    profile_cache.put("bob", "bge", 3, "b3")
    profile_cache.invalidate("alice")
    assert profile_cache.get("alice", "bge", 3) is None
    assert profile_cache.get("alice", "bge", 2) is None
    assert profile_cache.get("bob", "bge", 3) == "b3"  # untouched


def test_sweep_removes_expired_on_put(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(profile_cache.time, "monotonic", lambda: clock["t"])
    profile_cache.put("alice", "bge", 3, "a")
    # Move past both the TTL and the sweep interval, then a put on another key sweeps alice out.
    clock["t"] += profile_cache._TTL_SECONDS + profile_cache._SWEEP_INTERVAL_SECONDS + 1
    profile_cache.put("bob", "bge", 3, "b")
    assert ("alice", "bge", 3) not in profile_cache._entries
    assert profile_cache.get("bob", "bge", 3) == "b"


def test_clear_empties_everything() -> None:
    profile_cache.put("alice", "bge", 3, "a")
    profile_cache.put("bob", "bge", 3, "b")
    profile_cache.clear()
    assert profile_cache.get("alice", "bge", 3) is None
    assert profile_cache.get("bob", "bge", 3) is None
