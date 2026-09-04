"""In-process cache of a reader's For You profile (clusters + counts).

Keyed ``(user_sub, model_id, k)``; the value is opaque to this module. A profile is a function
of the reader's saved papers, positive events, and interest phrases - all of which change on a
write, not on a read - so the request path can serve a cached build instead of clustering every
time. Invalidation on the reader's writes keeps it fresh; a TTL backstop covers the one input
(the event log) that drifts without a write we hook.

Unlike :mod:`researchscout.api.ratelimit`, which is deliberately lock-free (single-step,
GIL-atomic dict ops), a get here may build-and-store and an invalidate scans every key for one
user, so a plain lock guards the map. A miss under concurrency may build twice; that is cheap
and correct, never wrong.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_TTL_SECONDS = 900.0
_SWEEP_INTERVAL_SECONDS = 60.0

_lock = threading.Lock()
# key -> (expires_at_monotonic, value)
_entries: dict[tuple[str, str, int], tuple[float, Any]] = {}
_last_sweep = 0.0


def get(user_sub: str, model_id: str, k: int) -> Any | None:
    """The cached profile for this reader/model/k, or None when absent or expired."""
    now = time.monotonic()
    key = (user_sub, model_id, k)
    with _lock:
        hit = _entries.get(key)
        if hit is None:
            return None
        expires_at, value = hit
        if expires_at <= now:
            del _entries[key]
            return None
        return value


def put(user_sub: str, model_id: str, k: int, value: Any) -> None:
    """Store a freshly built profile; sweeps expired entries on a timer, not every call."""
    now = time.monotonic()
    with _lock:
        _sweep(now)
        _entries[(user_sub, model_id, k)] = (now + _TTL_SECONDS, value)


def invalidate(user_sub: str) -> None:
    """Drop every cached profile for one reader, across model and k variants."""
    with _lock:
        for key in [key for key in _entries if key[0] == user_sub]:
            del _entries[key]


def clear() -> None:
    """Empty the whole cache and reset the sweep clock (tests, and belt-and-braces on shutdown)."""
    global _last_sweep
    with _lock:
        _entries.clear()
        _last_sweep = 0.0


def _sweep(now: float) -> None:
    """Drop expired entries; timer-gated so a busy minute does not walk the whole map each call."""
    global _last_sweep
    if now - _last_sweep < _SWEEP_INTERVAL_SECONDS:
        return
    _last_sweep = now
    for key in [key for key, (expires_at, _) in _entries.items() if expires_at <= now]:
        del _entries[key]
