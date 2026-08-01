"""How many answers the model may be generating at once.

The model runs on the same machine as Postgres and the API. Two people asking at the same time
is fine; five is not - the box starts swapping and every page on the site gets slow, not just
the answers. This caps concurrent generations and makes the wait explicit: a caller who cannot
get a slot within the queue timeout is told the service is busy rather than left hanging.

The endpoints are synchronous (FastAPI runs them in a threadpool), so this is a threading
primitive rather than an asyncio one.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from fastapi import HTTPException

from researchscout.config import get_settings


@lru_cache(maxsize=4)
def _slots(limit: int) -> threading.BoundedSemaphore:
    # Keyed by the configured limit so a settings change in tests gets its own semaphore
    # rather than silently reusing one sized for the old value.
    return threading.BoundedSemaphore(limit)


@contextmanager
def llm_slot() -> Iterator[None]:
    """Hold one generation slot for the duration of the block, or raise 503."""
    settings = get_settings()
    semaphore = _slots(max(1, settings.llm_max_concurrency))
    if not semaphore.acquire(timeout=settings.llm_queue_timeout_seconds):
        raise HTTPException(
            status_code=503,
            detail="the answer service is busy right now, try again in a moment",
            headers={"Retry-After": "10"},
        )
    try:
        yield
    finally:
        semaphore.release()
