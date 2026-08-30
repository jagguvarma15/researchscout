"""Grounded question answering over stored papers."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from openai import OpenAIError
from sqlalchemy.orm import Session

from researchscout.answer import answer, answer_fast
from researchscout.api.auth import User, optional_user, owner_tag
from researchscout.api.deps import get_embedder, get_llm, get_session
from researchscout.api.llmgate import llm_slot
from researchscout.api.ratelimit import check_rate_limit, client_key
from researchscout.api.routers.chat import record_metrics
from researchscout.api.schemas import AskRequest, AskResponse, UsedPaper
from researchscout.config import get_settings
from researchscout.embed.base import Embedder
from researchscout.llm.base import LLM
from researchscout.llm.errors import is_quota_error
from researchscout.llm.tracing import pipeline_run

router = APIRouter(tags=["ask"])


@router.post("/ask")
def ask(
    request: Request,
    body: AskRequest,
    user: Annotated[User | None, Depends(optional_user)],
    session: Annotated[Session, Depends(get_session)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(get_llm)],
) -> AskResponse:
    """Answer a question with a grounded, cited summary of recent papers.

    Same rule as the chat route: the extractive mode is open to anyone, generated answers need
    an account.
    """
    settings = get_settings()
    if user is None and body.mode != "fast":
        raise HTTPException(
            status_code=401,
            detail="sign in for generated answers; ask again for a quick one",
            headers={"WWW-Authenticate": "Bearer"},
        )
    check_rate_limit(
        client_key(request, user, prefix="ask"),
        limit=settings.chat_rate_limit if user else settings.chat_rate_limit_anonymous,
        window_seconds=settings.chat_rate_window_seconds,
    )
    started = time.perf_counter()
    user_hash = owner_tag(user.sub if user else None)
    if body.mode == "fast":
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
        record_metrics(
            mode="fast",
            surface="ask",
            question=body.question,
            retrieved=fast.retrieved,
            best_relevance=fast.best_relevance,
            found=fast.found,
            retrieve_ms=int(timings.get("embed_ms", 0.0) + timings.get("legs_ms", 0.0)),
            rerank_ms=int(timings["rerank_ms"]) if "rerank_ms" in timings else None,
            llm_ms=None,
            total_ms=int((time.perf_counter() - started) * 1000),
            outcome="ok" if fast.found else "notfound",
            user_hash=user_hash,
            pinned=body.paper_id is not None,
            rerank_used=any(item.relevance is not None for item in fast.answer.used)
            if fast.answer.used
            else None,
        )
        return AskResponse(
            text=fast.answer.text,
            cited=fast.answer.cited,
            hallucinated=fast.answer.hallucinated,
            used=[UsedPaper.from_scored(item) for item in fast.answer.used],
            found=fast.found,
        )

    def failed_row(outcome: str) -> None:
        record_metrics(
            mode="llm",
            surface="ask",
            question=body.question,
            retrieved=0,
            best_relevance=None,
            found=False,
            retrieve_ms=None,
            rerank_ms=None,
            llm_ms=None,
            total_ms=int((time.perf_counter() - started) * 1000),
            outcome=outcome,
            user_hash=user_hash,
            agentic=body.agentic,
            pinned=body.paper_id is not None,
        )

    run = pipeline_run(
        "ask",
        inputs={"question": body.question},
        tags=["ask", "agentic" if body.agentic else "single-shot"],
        metadata={"mode": "llm", "agentic": body.agentic, "k": body.k}
        | ({"paper_id": body.paper_id} if body.paper_id else {}),
    )
    timings = {}
    try:
        with llm_slot():
            result = answer(
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
            )
    except HTTPException:
        failed_row("busy")
        run.end(outputs={"outcome": "busy"})
        raise
    except OpenAIError as exc:
        failed_row("llm_error")
        run.end(outputs={"outcome": "llm_error"}, error=repr(exc)[:200])
        # A spent daily quota and a dead backend are different states to the caller.
        detail = (
            "LLM quota exhausted for today" if is_quota_error(exc) else "LLM backend unavailable"
        )
        raise HTTPException(status_code=502, detail=detail) from exc
    run.end(outputs={"outcome": "ok", "retrieved": len(result.used)})
    known = [item.relevance for item in result.used if item.relevance is not None]
    record_metrics(
        mode="llm",
        surface="ask",
        question=body.question,
        retrieved=len(result.used),
        best_relevance=max(known) if known else None,
        found=len(result.used) > 0,
        retrieve_ms=int(timings["retrieve_ms"]) if "retrieve_ms" in timings else None,
        rerank_ms=int(timings["rerank_ms"]) if "rerank_ms" in timings else None,
        llm_ms=int(timings["llm_ms"]) if "llm_ms" in timings else None,
        total_ms=int((time.perf_counter() - started) * 1000),
        outcome="ok",
        user_hash=user_hash,
        agentic=body.agentic,
        pinned=body.paper_id is not None,
        model=result.model,
        rerank_used=any(item.relevance is not None for item in result.used)
        if result.used
        else None,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        hallucinated=len(result.hallucinated),
    )
    return AskResponse(
        text=result.text,
        cited=result.cited,
        hallucinated=result.hallucinated,
        used=[UsedPaper.from_scored(item) for item in result.used],
        plan=result.plan,
    )
