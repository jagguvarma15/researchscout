"""Lightweight tracing seam.

A no-op by default: it emits a structured log line per span (suppressed unless the app configures
logging at INFO). This is enough to "instrument from day one" without a heavy dependency; a real
backend (e.g. Langfuse) can back this seam later without touching call sites.
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
