"""Grounded question answering over stored papers."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from openai import OpenAIError
from sqlalchemy.orm import Session

from researchscout.answer import answer, answer_fast
from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_embedder, get_llm, get_session
from researchscout.api.routers.chat import record_metrics
from researchscout.api.schemas import AskRequest, AskResponse, UsedPaper
from researchscout.embed.base import Embedder
from researchscout.llm.base import LLM

router = APIRouter(tags=["ask"])


@router.post("/ask")
def ask(
    body: AskRequest,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(get_llm)],
) -> AskResponse:
    """Answer a question with a grounded, cited summary of recent papers."""
    started = time.perf_counter()
    if body.mode == "fast":
        timings: dict[str, float] = {}
        fast = answer_fast(
            session, embedder, body.question, k=body.k, days=body.days, timings=timings
        )
        record_metrics(
            mode="fast",
            surface="ask",
            question=body.question,
            retrieved=len(fast.answer.used),
            best_relevance=fast.best_relevance,
            found=fast.found,
            retrieve_ms=int(timings.get("embed_ms", 0.0) + timings.get("legs_ms", 0.0)),
            rerank_ms=int(timings["rerank_ms"]) if "rerank_ms" in timings else None,
            llm_ms=None,
            total_ms=int((time.perf_counter() - started) * 1000),
        )
        return AskResponse(
            text=fast.answer.text,
            cited=fast.answer.cited,
            hallucinated=fast.answer.hallucinated,
            used=[UsedPaper.from_scored(item) for item in fast.answer.used],
            found=fast.found,
        )
    try:
        result = answer(
            session, embedder, llm, body.question, k=body.k, days=body.days, agentic=body.agentic
        )
    except OpenAIError as exc:
        raise HTTPException(status_code=502, detail="LLM backend unavailable") from exc
    known = [item.relevance for item in result.used if item.relevance is not None]
    record_metrics(
        mode="llm",
        surface="ask",
        question=body.question,
        retrieved=len(result.used),
        best_relevance=max(known) if known else None,
        found=len(result.used) > 0,
        retrieve_ms=None,
        rerank_ms=None,
        llm_ms=None,
        total_ms=int((time.perf_counter() - started) * 1000),
    )
    return AskResponse(
        text=result.text,
        cited=result.cited,
        hallucinated=result.hallucinated,
        used=[UsedPaper.from_scored(item) for item in result.used],
    )
