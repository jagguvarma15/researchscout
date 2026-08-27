"""How many forward passes the local models may be running at once.

The embedder and the cross-encoder are process-wide singletons shared by the API's request
threadpool, the scheduler thread and the CLI. Each encode allocates transient tensors, so
concurrency multiplies peak memory with no ceiling - the same failure ``api/llmgate`` caps
for generations, on the embed and rerank path instead.

This is deliberately not ``llmgate``: that gate refuses with a 503 when a caller cannot get
a slot, which only makes sense inside an HTTP request. A forward pass here is milliseconds
and half the callers (scheduler, stream worker, CLI) have no request to fail, so the slot
blocks until free instead.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from researchscout.config import get_settings


@lru_cache(maxsize=4)
def _slots(limit: int) -> threading.BoundedSemaphore:
    # Keyed by the configured limit so a settings change in tests gets its own semaphore
    # rather than silently reusing one sized for the old value.
    return threading.BoundedSemaphore(limit)


@contextmanager
def model_slot() -> Iterator[None]:
    """Hold one model-pass slot for the duration of the block, waiting as long as it takes."""
    semaphore = _slots(max(1, get_settings().embed_max_concurrency))
    semaphore.acquire()
    try:
        yield
    finally:
        semaphore.release()
