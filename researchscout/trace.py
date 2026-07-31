"""The tracing seam: one structured log line per traced block, plus process log setup.

Call sites are unchanged since day one: ``with trace_span(name, **fields) as span``. The block
times itself, the caller can add fields to the yielded dict, and everything lands in a single
log record on exit. Long-running entrypoints (the scheduler, the stream) call
``configure_logging`` once at startup so those records actually reach their log files.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("researchscout.trace")


def configure_logging(level: int = logging.INFO) -> None:
    """Timestamped stderr logging: the researchscout tree at ``level``, everything else WARNING.

    Keeping the root at WARNING mutes chatty third-party INFO (HTTP clients, model loading)
    while our own progress lines flow. Safe to call again: ``basicConfig`` is a no-op once the
    root logger has a handler, so re-entry never duplicates output.
    """
    logging.basicConfig(
        level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("researchscout").setLevel(level)


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
