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

_windows: dict[str, tuple[int, int]] = {}


def client_ip(request: Request) -> str:
    """The caller's address as the edge reports it.

    ``CF-Connecting-IP`` is set by Cloudflare and cannot be forged by the client, so it wins.
    ``X-Forwarded-For`` is a fallback for other proxies and is only as trustworthy as whatever
    sits in front of the app; a direct connection falls back to the socket address.
    """
    forwarded = request.headers.get("cf-connecting-ip")
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
