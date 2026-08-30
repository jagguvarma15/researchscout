"""Per-call LLM usage capture: who called the model, for what, and what it cost.

Every completion the app makes — an answer, a guardrail check, a digest, a topic label —
lands one row in the ``llm_usage`` ledger with its purpose, model, token counts, latency,
and outcome. The daily model budget is the binding constraint of the whole AI surface
(a free tier measured in requests per day), and until this ledger existed the count was a
hand-derived property of the code rather than a measurement. Always on, like the ask
metrics: a default-off ledger of the binding constraint would be blind by default.

The purpose travels via a :class:`~contextvars.ContextVar` set by the call site
(``with llm_purpose(PURPOSE_SYNTHESIS): llm.stream(...)``) and read by the client at call
time. Two rules keep that correct under the API's streaming responses, which Starlette
drives through a threadpool in per-resumption *copies* of the task context:

1. A ``llm_purpose`` block must never span a generator ``yield`` — the client reads the
   purpose eagerly when the call starts, so the block only needs to cover the call
   expression itself.
2. :func:`last_usage` is only meaningful inside the same resumption window that finished
   the call: copy it into a result object right after the loop that consumed the stream,
   and let downstream code read the result, never the contextvar.

Recording is best-effort in a session of its own: a metrics failure must never surface
into an answer.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# The shared purpose vocabulary: these strings are the ledger's purpose column AND the
# LangSmith step tags, defined once so the two views of a call never drift apart.
PURPOSE_GUARDRAIL = "guardrail"
PURPOSE_DECOMPOSE = "decompose"
PURPOSE_SYNTHESIS = "synthesis"
PURPOSE_DIGEST = "digest"
PURPOSE_TOPIC_LABEL = "topic_label"
PURPOSE_KEYWORD_FALLBACK = "keyword_fallback"
PURPOSE_CUSTOM_LABEL = "custom_label"

_purpose_var: ContextVar[str] = ContextVar("rs_llm_purpose", default="other")
_last_usage_var: ContextVar[LlmCallUsage | None] = ContextVar("rs_llm_last_usage", default=None)


@dataclass(frozen=True)
class LlmCallUsage:
    """What one model call cost: identity, tokens, latency, and how it ended."""

    purpose: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int
    outcome: str  # ok | quota | error | aborted
    detail: str | None  # trimmed exception repr on failure, None on success


@contextmanager
def llm_purpose(name: str) -> Iterator[None]:
    """Tag model calls made inside the block with ``name`` (never span a yield with it)."""
    token = _purpose_var.set(name)
    try:
        yield
    finally:
        _purpose_var.reset(token)


def current_purpose() -> str:
    """The purpose tag for a model call starting now ("other" outside any block)."""
    return _purpose_var.get()


def last_usage() -> LlmCallUsage | None:
    """The most recent call's usage in this context — read it in the same resumption window."""
    return _last_usage_var.get()


def record_usage(usage: LlmCallUsage) -> None:
    """Remember the call in-context and append it to the ledger, swallowing any failure."""
    _last_usage_var.set(usage)
    try:
        from researchscout.store.db import session_scope
        from researchscout.store.llm_usage import add_usage

        with session_scope() as session:
            add_usage(session, usage)
    except Exception:  # noqa: BLE001 - a metrics failure must never surface into an answer
        logger.warning("could not record llm usage", exc_info=True)
