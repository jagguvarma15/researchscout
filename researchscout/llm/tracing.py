"""LangSmith pipeline traces: one run tree per ask, a strict no-op when tracing is off.

The client wrap in ``openai_compat`` already traces every raw completion, but as
disconnected events — a /deep request reads as three unrelated model calls. This seam adds
the structure: a parent run per pipeline (an ask, a deep ask) with child steps for
guardrail, decompose, per-part retrieval, the reference hop, and synthesis, so the trace
reads as the tree it actually is.

Design notes, load-bearing:

- **Explicit handles, not decorators or ambient context.** ``langsmith`` is an optional
  dependency (the ``observe`` extra), so every import here is lazy and the disabled path
  is a no-op singleton with zero overhead. More importantly, the API's streaming answers
  are sync generators that Starlette resumes on a threadpool in per-resumption *copies*
  of the task context — an ambient contextvar entered in one resumption is invisible to
  the next, and exiting it there raises. The parent therefore lives as a plain object in
  the caller's scope, children attach by explicit parent, and ambient context is used
  only inside yield-free windows.
- **``ambient()`` must never span a generator yield.** It exists solely so the wrapped
  OpenAI client's auto-created LLM run nests under the right step; wrap the
  ``llm.complete(...)``/``llm.stream(...)`` call expression and nothing more (the client
  creates its request eagerly, so the call expression is where the run is born).
- **``trace_span`` coexists.** That seam is the always-on structured log line — it works
  with tracing off, which is the default posture. This one is the rich opt-in backend
  for the same moments; no call site trades one for the other.

``post()``/``patch()`` hand runs to the SDK's background batch client, so nothing here
blocks the request path.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

_warned_missing = False


def _enabled() -> bool:
    """Tracing is on only when asked for AND the optional package is importable."""
    global _warned_missing
    if os.environ.get("LANGSMITH_TRACING", "").lower() != "true":
        return False
    try:
        import langsmith  # noqa: F401
    except ImportError:
        if not _warned_missing:
            logger.warning(
                "LANGSMITH_TRACING is set but langsmith is not installed; pipeline traces off"
            )
            _warned_missing = True
        return False
    return True


class PipelineRun:
    """A handle on one LangSmith run tree; every method no-ops when tracing is off."""

    def __init__(self, run: Any | None) -> None:
        self._run = run
        self._outputs: dict[str, Any] = {}

    def out(self, **fields: Any) -> None:
        """Stage output fields to attach when this run (or step) closes."""
        if self._run is None:
            return
        self._outputs.update(fields)

    @contextmanager
    def step(
        self, name: str, *, run_type: str = "chain", inputs: dict[str, Any] | None = None
    ) -> Iterator[PipelineRun]:
        """A child run covering the block; an exception closes it as an error and re-raises."""
        if self._run is None:
            yield NOOP_RUN
            return
        child = self._run.create_child(name=name, run_type=run_type, inputs=inputs or {})
        child.post()
        handle = PipelineRun(child)
        try:
            yield handle
        except Exception as exc:
            child.end(error=repr(exc)[:500])
            child.patch()
            raise
        child.end(outputs=handle._outputs or None)
        child.patch()

    @contextmanager
    def ambient(self) -> Iterator[None]:
        """Attach the wrapped client's auto-runs to this run; never span a yield with it."""
        if self._run is None:
            yield
            return
        from langsmith.run_helpers import tracing_context

        with tracing_context(parent=self._run):
            yield

    def end(self, *, outputs: dict[str, Any] | None = None, error: str | None = None) -> None:
        """Close the run with its staged and final outputs (call exactly once, in a finally)."""
        if self._run is None:
            return
        merged = dict(self._outputs)
        if outputs:
            merged.update(outputs)
        self._run.end(outputs=merged or None, error=error)
        self._run.patch()


NOOP_RUN = PipelineRun(None)


def pipeline_run(
    name: str,
    *,
    inputs: dict[str, Any] | None = None,
    tags: Sequence[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> PipelineRun:
    """Open a parent run, or the no-op handle when tracing is off or unavailable."""
    if not _enabled():
        return NOOP_RUN
    try:
        from langsmith.run_trees import RunTree

        run = RunTree(
            name=name,
            run_type="chain",
            inputs=inputs or {},
            tags=list(tags or []),
            extra={"metadata": dict(metadata or {})},
        )
        run.post()
    except Exception:  # noqa: BLE001 - tracing must never take an answer down with it
        logger.warning("could not start a LangSmith run", exc_info=True)
        return NOOP_RUN
    return PipelineRun(run)
