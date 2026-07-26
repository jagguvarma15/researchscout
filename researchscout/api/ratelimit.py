"""Fixed-window rate limiting, in process (a dict of window counters).

Per-process by design: `scout serve api` runs a single uvicorn process, so one counter map is
the whole picture. Running multiple workers would multiply the effective limit.
"""

from __future__ import annotations

import time

from fastapi import HTTPException

_windows: dict[str, tuple[int, int]] = {}


def check_rate_limit(key: str, *, limit: int, window_seconds: int) -> None:
    """Raise 429 (with Retry-After) once ``key`` exceeds ``limit`` hits in the current window."""
    now = int(time.time())
    window = now // window_seconds
    stored_window, count = _windows.get(key, (window, 0))
    if stored_window != window:
        count = 0
    count += 1
    _windows[key] = (window, count)
    if count > limit:
        retry_after = window_seconds - (now % window_seconds)
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
