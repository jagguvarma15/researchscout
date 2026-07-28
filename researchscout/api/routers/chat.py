"""Streaming chat over stored papers (server-sent events).

Event order: ``meta`` (what retrieval found) -> ``token`` deltas -> ``done`` with the citation
post-check, or ``error``. Guardrail refusals skip ``meta``: one ``token`` carrying the refusal
text, then an empty ``done``. The generator is synchronous; Starlette drives it from a
threadpool, and the request-scoped session stays open until the stream finishes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from openai import OpenAIError
from sqlalchemy.orm import Session

from researchscout.answer import Answer, StreamDelta, StreamMeta, answer_stream
from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_embedder, get_llm, get_session
from researchscout.api.ratelimit import check_rate_limit
from researchscout.api.schemas import AskRequest, UsedPaper
from researchscout.config import get_settings
from researchscout.embed.base import Embedder
from researchscout.guardrail import REFUSAL_TEXT, is_research_question
from researchscout.llm.base import LLM

router = APIRouter(tags=["chat"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    # Tell buffering proxies (nginx and friends) to pass events through as they come.
    "X-Accel-Buffering": "no",
}


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


@router.post("/chat")
def chat(
    body: AskRequest,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(get_llm)],
) -> StreamingResponse:
    """Stream a grounded, cited answer as SSE."""
    settings = get_settings()
    check_rate_limit(
        f"chat:{user.sub}",
        limit=settings.chat_rate_limit,
        window_seconds=settings.chat_rate_window_seconds,
    )

    def events() -> Iterator[str]:
        # Scope check inside the generator so SSE headers flush first and the client can show
        # progress during classify latency. Refusals reuse the token/done shape with no meta
        # event (no retrieval happened), so clients need no extra handling.
        if settings.chat_guardrail and not is_research_question(llm, body.question):
            yield _sse("token", {"delta": REFUSAL_TEXT})
            yield _sse("done", {"cited": [], "hallucinated": [], "used": []})
            return
        try:
            for event in answer_stream(
                session, embedder, llm, body.question, k=body.k, days=body.days
            ):
                if isinstance(event, StreamMeta):
                    yield _sse("meta", {"retrieved": event.retrieved})
                elif isinstance(event, StreamDelta):
                    yield _sse("token", {"delta": event.text})
                elif isinstance(event, Answer):
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
        except OpenAIError:
            yield _sse("error", {"code": 502, "message": "LLM backend unavailable"})

    return StreamingResponse(events(), media_type="text/event-stream", headers=_SSE_HEADERS)
