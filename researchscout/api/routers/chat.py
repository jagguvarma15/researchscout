"""Streaming chat over stored papers (server-sent events).

Event order: ``meta`` (what retrieval found) -> ``token`` deltas -> ``done`` with the citation
post-check, or ``error``. Guardrail refusals skip ``meta``: one ``token`` carrying the refusal
text, then an empty ``done``. Fast mode (``mode: "fast"``) answers extractively with no LLM
call and no guardrail: ``meta`` (with ``mode``) -> ``results`` (the structured entries the
drawer renders as cards) -> one ``token`` carrying the same content as text -> ``done`` when
something matched, or ``meta`` -> ``notfound`` -> empty ``done`` below the relevance floor. The
generator is synchronous; Starlette drives it from a threadpool, and the request-scoped
session stays open until the stream finishes.

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

from researchscout.answer import Answer, StreamDelta, StreamMeta, answer_fast, answer_stream
from researchscout.api.auth import User, optional_user
from researchscout.api.deps import get_embedder, get_llm, get_session
from researchscout.api.llmgate import llm_slot
from researchscout.api.ratelimit import check_rate_limit, client_key
from researchscout.api.schemas import AskRequest, FastResultItem, UsedPaper
from researchscout.config import get_settings
from researchscout.embed.base import Embedder
from researchscout.guardrail import REFUSAL_TEXT, is_research_question
from researchscout.llm.base import LLM
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

    def events() -> Iterator[str]:
        if body.mode == "fast":
            yield from _fast_events(session, embedder, body)
            return
        # Scope check inside the generator so SSE headers flush first and the client can show
        # progress during classify latency. Refusals reuse the token/done shape with no meta
        # event (no retrieval happened), so clients need no extra handling.
        if settings.chat_guardrail and not is_research_question(llm, body.question):
            yield _sse("token", {"delta": REFUSAL_TEXT})
            yield _sse("done", {"cited": [], "hallucinated": [], "used": []})
            return
        started = time.perf_counter()
        meta_at: float | None = None
        retrieved = 0
        final: Answer | None = None
        try:
            # The slot is held for the whole generation, not just its start, which is what
            # actually bounds how much of the machine the model can take.
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
                ):
                    if isinstance(event, StreamMeta):
                        meta_at = time.perf_counter()
                        retrieved = event.retrieved
                        yield _sse("meta", {"retrieved": event.retrieved})
                    elif isinstance(event, StreamDelta):
                        yield _sse("token", {"delta": event.text})
                    elif isinstance(event, Answer):
                        final = event
                        yield _sse(
                            "done",
                            {
                                "cited": event.cited,
                                "hallucinated": event.hallucinated,
                                "used": [
                                    UsedPaper.from_scored(item).model_dump() for item in event.used
                                ],
                            },
                        )
        except HTTPException as exc:
            # Headers are already on the wire, so a busy queue is an SSE error rather than a
            # status code.
            yield _sse("error", {"code": exc.status_code, "message": str(exc.detail)})
            return
        except OpenAIError:
            yield _sse("error", {"code": 502, "message": "LLM backend unavailable"})
            return
        ended = time.perf_counter()
        known = [
            item.relevance for item in (final.used if final else []) if item.relevance is not None
        ]
        record_metrics(
            mode="llm",
            surface="chat",
            question=body.question,
            retrieved=retrieved,
            best_relevance=max(known) if known else None,
            found=retrieved > 0,
            retrieve_ms=int((meta_at - started) * 1000) if meta_at else None,
            rerank_ms=None,
            llm_ms=int((ended - meta_at) * 1000) if meta_at else None,
            total_ms=int((ended - started) * 1000),
        )

    return StreamingResponse(events(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _fast_events(session: Session, embedder: Embedder, body: AskRequest) -> Iterator[str]:
    """The extractive fast path: no LLM, no guardrail (deterministic output needs none)."""
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
        retrieved=len(result.used),
        best_relevance=fast.best_relevance,
        found=fast.found,
        retrieve_ms=int(retrieve_ms) if timings else None,
        rerank_ms=int(timings["rerank_ms"]) if "rerank_ms" in timings else None,
        llm_ms=None,
        total_ms=int((time.perf_counter() - started) * 1000),
    )
