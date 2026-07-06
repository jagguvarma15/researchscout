"""Fixed-window rate limiting on Redis (atomic INCR + EXPIRE per window)."""

from __future__ import annotations

import logging
import time
from functools import lru_cache

from fastapi import HTTPException
from redis import Redis
from redis.exceptions import RedisError

from researchscout.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _redis() -> Redis:
    return Redis.from_url(get_settings().redis_url)


def check_rate_limit(key: str, *, limit: int, window_seconds: int) -> None:
    """Raise 429 (with Retry-After) once ``key`` exceeds ``limit`` hits in the current window.

    Fails open when Redis is unreachable: chat degrading to unlimited beats chat being down.
    """
    now = int(time.time())
    bucket = f"rl:{key}:{now // window_seconds}"
    try:
        pipe = _redis().pipeline()
        pipe.incr(bucket)
        pipe.expire(bucket, window_seconds)
        count = int(pipe.execute()[0])
    except RedisError:
        logger.warning("rate limiter unavailable, allowing request for %s", key)
        return
    if count > limit:
        retry_after = window_seconds - (now % window_seconds)
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
