"""The tracing seam: one structured log line per traced block.

Call sites are unchanged since day one: ``with trace_span(name, **fields) as span``. The block
times itself, the caller can add fields to the yielded dict, and everything lands in a single
log record on exit.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("researchscout.trace")


@contextmanager
def trace_span(name: str, **fields: Any) -> Iterator[dict[str, Any]]:
    """Time a block and log its fields. Yields a mutable dict the caller can add fields to."""
    span: dict[str, Any] = dict(fields)
    start = time.perf_counter()
    try:
        yield span
    finally:
        span["elapsed_ms"] = round((time.perf_counter() - start) * 1000.0, 1)
        logger.info("span %s %s", name, span)
