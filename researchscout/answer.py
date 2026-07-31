"""Grounded, cited answers: retrieve within the freshness window, then synthesize citing only those.

The synthesis is constrained to the retrieved papers, and a post-check drops any cited id the model
invented (a hallucinated citation never survives into ``Answer.cited``).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy.orm import Session

from researchscout.config import get_settings
from researchscout.embed.base import Embedder
from researchscout.llm.base import LLM
from researchscout.retrieve.search import ScoredPaper, retrieve
from researchscout.store.chunks import best_chunk_texts
from researchscout.trace import trace_span

_SYSTEM_PROMPT = (
    "You are a research assistant for AI, computer science, and adjacent technical fields. "
    "Answer ONLY from the papers provided below. "
    "For every claim, cite the paper id in square brackets, e.g. [arxiv:2401.12345]. "
    "Never invent ids or facts. If the papers do not cover the question, say so plainly. "
    "If the question is not about research in these fields, politely decline instead of "
    "answering. "
    "For each relevant paper, briefly cover what's new, why it matters, and what it beats."
)

_CITATION_RE = re.compile(r"\[([a-z]+:[^\]]+)\]")


@dataclass
class Answer:
    text: str
    cited: list[str]
    hallucinated: list[str]
    used: list[ScoredPaper]


@dataclass
class StreamMeta:
    """First stream event: what retrieval found, before any generation."""

    retrieved: int
    used: list[ScoredPaper]


@dataclass
class StreamDelta:
    """A chunk of generated answer text."""

    text: str


# Keeps an 8-paper prompt inside Ollama's default 4096-token window even with excerpts.
_EXCERPT_CHARS = 600
# Sections are capped so an enriched prompt still fits the same window.
_SECTIONS_IN_CONTEXT = 8


def _context(papers: list[ScoredPaper], quotes: dict[str, str] | None = None) -> str:
    parts = []
    for item in papers:
        block = f"[{item.paper.id}] {item.paper.title}\n{item.paper.abstract}"
        if item.paper.keywords:
            block += "\nKeywords: " + ", ".join(item.paper.keywords)
        if item.paper.sections:
            block += "\nSections: " + "; ".join(item.paper.sections[:_SECTIONS_IN_CONTEXT])
        if item.paper.labels:
            block += "\nLabels: " + ", ".join(label.label for label in item.paper.labels)
        quote = (quotes or {}).get(item.paper.id)
        if quote:
            block += f"\nExcerpt: {quote[:_EXCERPT_CHARS]}"
        parts.append(block)
    return "\n\n".join(parts)


def _excerpts_for(
    session: Session, embedder: Embedder, question: str, used: list[ScoredPaper]
) -> dict[str, str]:
    """Best-chunk excerpts when chunk retrieval is on (empty when off or nothing indexed)."""
    if not used or not get_settings().chunk_retrieval:
        return {}
    return best_chunk_texts(
        session,
        embedder.embed_query(question),
        [item.paper.id for item in used],
        model_id=embedder.model_id,
    )


def _post_check(text: str, used: list[ScoredPaper]) -> Answer:
    """Split found citations into retrieved vs invented (invented never survive)."""
    found = list(dict.fromkeys(_CITATION_RE.findall(text)))
    valid = {item.paper.id for item in used}
    cited = [cid for cid in found if cid in valid]
    hallucinated = [cid for cid in found if cid not in valid]
    return Answer(text=text, cited=cited, hallucinated=hallucinated, used=used)


def _retrieve_for(
    session: Session,
    embedder: Embedder,
    llm: LLM,
    question: str,
    *,
    k: int,
    days: int | None,
    agentic: bool,
) -> list[ScoredPaper]:
    """Agentic multi-hop retrieval when asked, otherwise the single-shot hybrid search."""
    if agentic:
        from researchscout.agentic import agentic_retrieve

        return agentic_retrieve(session, embedder, llm, question, k=k, days=days)
    return retrieve(session, embedder, question, k=k, days=days)


def answer_stream(
    session: Session,
    embedder: Embedder,
    llm: LLM,
    question: str,
    *,
    k: int = 8,
    days: int | None = None,
    agentic: bool = False,
) -> Iterator[StreamMeta | StreamDelta | Answer]:
    """Streaming variant of :func:`answer`: meta first, then deltas, then the final Answer.

    The final :class:`Answer` carries the citation post-check over the accumulated text — the
    same guarantee as the non-streaming path, it just arrives after the last delta.
    """
    with trace_span(
        "ask", question=question, k=k, days=days, streaming=True, agentic=agentic
    ) as span:
        used = _retrieve_for(session, embedder, llm, question, k=k, days=days, agentic=agentic)
        span["retrieved"] = len(used)
        yield StreamMeta(retrieved=len(used), used=used)
        if not used:
            empty = "No recent papers match this question."
            yield StreamDelta(text=empty)
            yield Answer(text=empty, cited=[], hallucinated=[], used=[])
            return

        quotes = _excerpts_for(session, embedder, question, used)
        user_prompt = f"Question: {question}\n\nPapers:\n{_context(used, quotes)}"
        parts: list[str] = []
        for delta in llm.stream(_SYSTEM_PROMPT, user_prompt):
            parts.append(delta)
            yield StreamDelta(text=delta)
        span["model"] = llm.model

        result = _post_check("".join(parts), used)
        span["cited"] = len(result.cited)
        span["hallucinated"] = len(result.hallucinated)
        yield result


def answer(
    session: Session,
    embedder: Embedder,
    llm: LLM,
    question: str,
    *,
    k: int = 8,
    days: int | None = None,
    agentic: bool = False,
) -> Answer:
    """Retrieve recent papers and synthesize a grounded, cited answer."""
    with trace_span("ask", question=question, k=k, days=days, agentic=agentic) as span:
        used = _retrieve_for(session, embedder, llm, question, k=k, days=days, agentic=agentic)
        span["retrieved"] = len(used)
        if not used:
            return Answer(
                text="No recent papers match this question.", cited=[], hallucinated=[], used=[]
            )

        quotes = _excerpts_for(session, embedder, question, used)
        user_prompt = f"Question: {question}\n\nPapers:\n{_context(used, quotes)}"
        text = llm.complete(_SYSTEM_PROMPT, user_prompt)
        span["model"] = llm.model

        result = _post_check(text, used)
        span["cited"] = len(result.cited)
        span["hallucinated"] = len(result.hallucinated)
        return result
