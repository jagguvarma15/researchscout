"""Streaming chat over stored papers (server-sent events).

Event order: ``plan`` (agentic sub-questions, only when a deep ask decomposed) -> ``meta``
(what retrieval found) -> ``token`` deltas -> ``done`` with the citation post-check plus the
model and token cost, or ``error`` (whose ``kind`` tells busy, quota, and unavailable
apart). Guardrail refusals skip ``meta``: one ``token`` carrying the refusal text, then an
empty ``done``. Fast mode (``mode: "fast"``) answers extractively with no LLM call and no
guardrail: ``meta`` (with ``mode``) -> ``results`` (the structured entries the drawer
renders as cards) -> one ``token`` carrying the same content as text -> ``done`` when
something matched, or ``meta`` -> ``notfound`` -> empty ``done`` below the relevance floor.
Every path - refusal, busy, quota, error included - records an ask_metrics row with its
outcome. The generator is synchronous; Starlette drives it from a threadpool, and the
request-scoped session stays open until the stream finishes.

Conversation ``history`` reaches the LLM path only: it shapes retrieval for short follow-ups
and joins the one model prompt. Fast mode is single-shot by design — extractive answers have
no model to resolve context with, and blending stale turn terms into the lexical leg would
degrade precision unpredictably — so it accepts the field and ignores it. ``paper_id`` scopes
either mode to one stored paper.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from openai import OpenAIError
from sqlalchemy.orm import Session

from researchscout.answer import (
    Answer,
    StreamDelta,
    StreamMeta,
    StreamPlan,
    answer_fast,
    answer_stream,
)
from researchscout.api.auth import User, optional_user, owner_tag
from researchscout.api.deps import get_embedder, get_llm, get_session
from researchscout.api.llmgate import llm_slot
from researchscout.api.ratelimit import check_rate_limit, client_key
from researchscout.api.schemas import AskRequest, FastResultItem, UsedPaper
from researchscout.config import get_settings
from researchscout.embed.base import Embedder
from researchscout.guardrail import REFUSAL_TEXT, is_research_question
from researchscout.llm.base import LLM
from researchscout.llm.errors import is_quota_error
from researchscout.llm.tracing import pipeline_run
from researchscout.store.ask_metrics import record_ask

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


def record_metrics(**fields: Any) -> None:
    """Best-effort metrics write in a session of its own, after the stream has finished.

    The request session is owned by the streaming response (and may be mid-error), so
    recording never touches it - and a metrics failure never surfaces to the user.
    """
    from researchscout.store.db import session_scope

    try:
        with session_scope() as metrics_session:
            record_ask(metrics_session, **fields)
    except Exception:  # noqa: BLE001 - metrics are never worth an error
        logger.warning("could not record ask metrics", exc_info=True)


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    # Tell buffering proxies (nginx and friends) to pass events through as they come.
    "X-Accel-Buffering": "no",
}


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


@router.post("/chat")
def chat(
    request: Request,
    body: AskRequest,
    user: Annotated[User | None, Depends(optional_user)],
    session: Annotated[Session, Depends(get_session)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(get_llm)],
) -> StreamingResponse:
    """Stream a grounded, cited answer as SSE.

    A signed-out visitor gets the extractive path only: it is the fast one, it costs no model
    time, and it is what makes the site useful before anyone registers. Generated answers need
    an account, both to keep one visitor from occupying the model and because the account is
    what the rate limit can hold on to.
    """
    settings = get_settings()
    if user is None and body.mode != "fast":
        raise HTTPException(
            status_code=401,
            detail="sign in for generated answers; ask again for a quick one",
            headers={"WWW-Authenticate": "Bearer"},
        )
    check_rate_limit(
        client_key(request, user, prefix="chat"),
        limit=settings.chat_rate_limit if user else settings.chat_rate_limit_anonymous,
        window_seconds=settings.chat_rate_window_seconds,
    )

    user_hash = owner_tag(user.sub if user else None)

    def _llm_row(**overrides: Any) -> dict[str, Any]:
        """The metrics-row fields every llm-mode path shares; overrides fill the rest."""
        fields: dict[str, Any] = {
            "mode": "llm",
            "surface": "chat",
            "question": body.question,
            "retrieved": 0,
            "best_relevance": None,
            "found": False,
            "retrieve_ms": None,
            "rerank_ms": None,
            "llm_ms": None,
            "user_hash": user_hash,
            "agentic": body.agentic,
            "pinned": body.paper_id is not None,
        }
        fields.update(overrides)
        return fields

    def events() -> Iterator[str]:
        if body.mode == "fast":
            yield from _fast_events(session, embedder, body, user_hash=user_hash)
            return
        run = pipeline_run(
            "ask",
            inputs={"question": body.question},
            tags=["chat", "agentic" if body.agentic else "single-shot"],
            metadata={"mode": "llm", "agentic": body.agentic, "k": body.k}
            | ({"paper_id": body.paper_id} if body.paper_id else {}),
        )
        started = time.perf_counter()
        outcome = "aborted"  # a client disconnect skips every terminal path below
        retrieved = 0
        try:
            # Scope check inside the generator so SSE headers flush first and the client
            # can show progress during classify latency. Refusals reuse the token/done
            # shape with no meta event (no retrieval happened).
            if settings.chat_guardrail and not is_research_question(llm, body.question, run=run):
                yield _sse("token", {"delta": REFUSAL_TEXT})
                yield _sse("done", {"cited": [], "hallucinated": [], "used": []})
                outcome = "refused"
                record_metrics(
                    **_llm_row(
                        outcome="refused",
                        total_ms=int((time.perf_counter() - started) * 1000),
                    )
                )
                return
            first_token_at: float | None = None
            final: Answer | None = None
            timings: dict[str, float] = {}
            try:
                # The slot is held for the whole generation, not just its start, which is
                # what actually bounds how much of the machine the model can take.
                with llm_slot():
                    for event in answer_stream(
                        session,
                        embedder,
                        llm,
                        body.question,
                        k=body.k,
                        days=body.days,
                        agentic=body.agentic,
                        history=[(turn.role, turn.text) for turn in body.history],
                        paper_id=body.paper_id,
                        timings=timings,
                        trace=run,
                    ):
                        if isinstance(event, StreamPlan):
                            yield _sse("plan", {"parts": event.parts})
                        elif isinstance(event, StreamMeta):
                            retrieved = event.retrieved
                            yield _sse(
                                "meta",
                                {
                                    "retrieved": event.retrieved,
                                    "mode": "llm",
                                    "agentic": body.agentic,
                                },
                            )
                        elif isinstance(event, StreamDelta):
                            if first_token_at is None:
                                first_token_at = time.perf_counter()
                            yield _sse("token", {"delta": event.text})
                        elif isinstance(event, Answer):
                            final = event
                            yield _sse(
                                "done",
                                {
                                    "cited": event.cited,
                                    "hallucinated": event.hallucinated,
                                    "used": [
                                        UsedPaper.from_scored(item).model_dump()
                                        for item in event.used
                                    ],
                                    "model": event.model,
                                    "prompt_tokens": event.prompt_tokens,
                                    "completion_tokens": event.completion_tokens,
                                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                                },
                            )
            except HTTPException as exc:
                # Headers are already on the wire, so a busy queue is an SSE error rather
                # than a status code.
                yield _sse(
                    "error",
                    {"code": exc.status_code, "kind": "busy", "message": str(exc.detail)},
                )
                outcome = "busy"
                record_metrics(
                    **_llm_row(outcome="busy", total_ms=int((time.perf_counter() - started) * 1000))
                )
                return
            except OpenAIError as exc:
                # A spent daily quota and a dead backend are different states to a reader:
                # one means "come back tomorrow, fast answers still work".
                quota = is_quota_error(exc)
                yield _sse(
                    "error",
                    {
                        "code": 502,
                        "kind": "quota" if quota else "unavailable",
                        "message": "AI quota exhausted for today - fast answers still work"
                        if quota
                        else "LLM backend unavailable",
                    },
                )
                outcome = "llm_error"
                retrieve_ms = timings.get("retrieve_ms")
                record_metrics(
                    **_llm_row(
                        outcome="llm_error",
                        retrieved=retrieved,
                        retrieve_ms=int(retrieve_ms) if retrieve_ms is not None else None,
                        total_ms=int((time.perf_counter() - started) * 1000),
                    )
                )
                return
            ended = time.perf_counter()
            outcome = "ok"
            used = final.used if final else []
            known = [item.relevance for item in used if item.relevance is not None]
            record_metrics(
                **_llm_row(
                    outcome="ok",
                    retrieved=retrieved,
                    best_relevance=max(known) if known else None,
                    found=retrieved > 0,
                    retrieve_ms=int(timings["retrieve_ms"]) if "retrieve_ms" in timings else None,
                    rerank_ms=int(timings["rerank_ms"]) if "rerank_ms" in timings else None,
                    llm_ms=int(timings["llm_ms"]) if "llm_ms" in timings else None,
                    total_ms=int((ended - started) * 1000),
                    model=final.model if final else None,
                    rerank_used=any(item.relevance is not None for item in used) if used else None,
                    prompt_tokens=final.prompt_tokens if final else None,
                    completion_tokens=final.completion_tokens if final else None,
                    first_token_ms=int((first_token_at - started) * 1000)
                    if first_token_at
                    else None,
                    hallucinated=len(final.hallucinated) if final else None,
                )
            )
        finally:
            # Closes the trace on completion, on either error path, and on a client
            # disconnect (GeneratorExit runs this too).
            run.end(outputs={"outcome": outcome, "retrieved": retrieved})

    return StreamingResponse(events(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _fast_events(
    session: Session, embedder: Embedder, body: AskRequest, *, user_hash: str | None = None
) -> Iterator[str]:
    """The extractive fast path: no LLM, no guardrail (deterministic output needs none).

    No pipeline run either, on purpose: LangSmith is model observability, and a zero-LLM
    ask at anonymous volume would spend the trace budget saying nothing - trace_span
    already logs these.
    """
    started = time.perf_counter()
    timings: dict[str, float] = {}
    fast = answer_fast(
        session,
        embedder,
        body.question,
        k=body.k,
        days=body.days,
        paper_id=body.paper_id,
        timings=timings,
    )
    result = fast.answer
    yield _sse("meta", {"retrieved": len(result.used), "mode": "fast"})
    if not fast.found:
        yield _sse(
            "notfound",
            {"query": body.question, "web_search": get_settings().web_search_enabled},
        )
        yield _sse("done", {"cited": [], "hallucinated": [], "used": []})
    else:
        # results precedes token so legacy consumers that only read token/done see
        # identical content while the drawer renders the structured cards instead.
        yield _sse(
            "results",
            {
                "items": [
                    FastResultItem.from_entry(entry).model_dump(mode="json")
                    for entry in fast.entries
                ]
            },
        )
        yield _sse("token", {"delta": result.text})
        yield _sse(
            "done",
            {
                "cited": result.cited,
                "hallucinated": result.hallucinated,
                "used": [UsedPaper.from_scored(item).model_dump() for item in result.used],
            },
        )
    retrieve_ms = timings.get("embed_ms", 0.0) + timings.get("legs_ms", 0.0)
    record_metrics(
        mode="fast",
        surface="chat",
        question=body.question,
        retrieved=fast.retrieved,
        best_relevance=fast.best_relevance,
        found=fast.found,
        retrieve_ms=int(retrieve_ms) if timings else None,
        rerank_ms=int(timings["rerank_ms"]) if "rerank_ms" in timings else None,
        llm_ms=None,
        total_ms=int((time.perf_counter() - started) * 1000),
        outcome="ok" if fast.found else "notfound",
        user_hash=user_hash,
        pinned=body.paper_id is not None,
        rerank_used=any(item.relevance is not None for item in result.used)
        if result.used
        else None,
    )
