"""Fixed-window rate limiting, in process (a dict of window counters).

Per-process by design: `scout serve api` runs a single uvicorn process, so one counter map is
the whole picture. Running multiple workers would multiply the effective limit.

Buckets are keyed per account, and per client address for callers who have none - one shared
anonymous bucket would let a single visitor spend everyone else's allowance.
"""

from __future__ import annotations

import time

from fastapi import HTTPException, Request

from researchscout.api.auth import User
from researchscout.api.service_auth import CLIENT_IP_HEADER

#: key -> (epoch second the current window closes, hits so far in it). The expiry is stored
#: rather than the window number because the number is only meaningful alongside the window
#: length, and the two limiters here use different ones.
_windows: dict[str, tuple[int, int]] = {}

#: How often closed windows are swept out of the map. On a timer rather than on every call:
#: sweeping walks the whole map, and a busy minute should not do that thousands of times.
_SWEEP_INTERVAL_SECONDS = 60
_last_sweep = 0


def client_ip(request: Request) -> str:
    """Who to hold responsible for this request.

    Every browser request reaches the API through the site's own server, so the socket address
    is that server's, not the visitor's - taken at face value it would put every signed-out
    visitor in one bucket, which is the same as having no limit at all. The proxy therefore
    forwards the visitor's address, and it is believed only for a request that proved it came
    from the site (see api/service_auth.py). Anything else falls back to the socket address,
    because a header anyone can set is a fresh bucket for the asking.
    """
    if getattr(request.state, "trusted_proxy", False):
        forwarded = request.headers.get(CLIENT_IP_HEADER)
        if forwarded:
            return forwarded.strip()
        chain = request.headers.get("x-forwarded-for")
        if chain:
            return chain.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def client_key(request: Request, user: User | None, *, prefix: str) -> str:
    """A limiter key for this caller: their account, or their address when signed out."""
    if user is not None:
        return f"{prefix}:{user.sub}"
    return f"{prefix}:ip:{client_ip(request)}"


def _sweep(now: int) -> None:
    """Drop windows that have closed.

    Without this the map gains an entry per key and loses none. Most keys are client addresses,
    and once the API has a public hostname those arrive from anything that scans it - so the
    map grows for the life of the process, holding counters for windows that ended weeks ago.
    """
    global _last_sweep
    if now - _last_sweep < _SWEEP_INTERVAL_SECONDS:
        return
    _last_sweep = now
    for key in [key for key, (expires_at, _) in _windows.items() if expires_at <= now]:
        del _windows[key]


def check_rate_limit(key: str, *, limit: int, window_seconds: int) -> None:
    """Raise 429 (with Retry-After) once ``key`` exceeds ``limit`` hits in the current window."""
    now = int(time.time())
    expires_at = (now // window_seconds + 1) * window_seconds
    stored_expiry, count = _windows.get(key, (expires_at, 0))
    if stored_expiry != expires_at:
        count = 0
    count += 1
    _windows[key] = (expires_at, count)
    _sweep(now)
    if count > limit:
        retry_after = expires_at - now
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
