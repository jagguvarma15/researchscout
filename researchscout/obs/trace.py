"""The tracing seam, now with real backends behind the same one-line API.

Call sites are unchanged since day one: ``with trace_span(name, **fields) as span``. The block
always emits the structured log line; when OTel is enabled the block also becomes a span (the
dict's fields become attributes on exit), and when LangSmith tracing is enabled it becomes a
LangSmith run (inputs = the call fields, outputs = whatever the block added).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Any

from researchscout.config import get_settings

logger = logging.getLogger("researchscout.trace")

_ATTR_TYPES = (str, bool, int, float)


def _langsmith_enabled() -> bool:
    return os.environ.get("LANGSMITH_TRACING", "").lower() == "true"


@contextmanager
def trace_span(name: str, **fields: Any) -> Iterator[dict[str, Any]]:
    """Time a block and log its fields. Yields a mutable dict the caller can add fields to."""
    span: dict[str, Any] = dict(fields)
    start = time.perf_counter()
    with ExitStack() as stack:
        otel_span = None
        if get_settings().otel_enabled:
            from opentelemetry import trace

            otel_span = stack.enter_context(
                trace.get_tracer("researchscout").start_as_current_span(name)
            )
        ls_run = None
        if _langsmith_enabled():
            from langsmith import trace as ls_trace

            ls_run = stack.enter_context(ls_trace(name=name, run_type="chain", inputs=dict(fields)))
        try:
            yield span
        finally:
            span["elapsed_ms"] = round((time.perf_counter() - start) * 1000.0, 1)
            if otel_span is not None:
                for key, value in span.items():
                    if isinstance(value, _ATTR_TYPES):
                        otel_span.set_attribute(key, value)
                    elif value is not None:
                        otel_span.set_attribute(key, str(value))
            if ls_run is not None:
                outputs = {key: value for key, value in span.items() if key not in fields}
                ls_run.end(outputs=outputs)
            logger.info("span %s %s", name, span)
