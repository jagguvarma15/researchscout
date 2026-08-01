"""Grounded, cited answers: retrieve within the freshness window, then synthesize citing only those.

The synthesis is constrained to the retrieved papers, and a post-check drops any cited id the model
invented (a hallucinated citation never survives into ``Answer.cited``).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

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

# The fast extractive answer shows at most this many papers with shorter excerpts.
_FAST_SHOWN = 5
_FAST_EXCERPT_CHARS = 300
_FAST_MATCH_TERMS = 6
# Lexical-only hits carry the sentinel cosine distance 1.0: relevance unknowable, never
# counted against the threshold.
_LEXICAL_SENTINEL = 0.999
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_NOT_FOUND_TEXT = "No papers in the library matched this question closely enough."


@dataclass
class FastEntry:
    """One structured fast-answer hit; the rendered text derives from these."""

    id: str
    title: str
    published_at: datetime
    venue: str | None
    matches: list[str]
    keywords: list[str]
    excerpt: str | None
    relevance: float | None


@dataclass
class FastAnswer:
    """The extractive result plus the threshold verdict the router acts on."""

    answer: Answer
    found: bool
    best_relevance: float | None
    # Empty on the not-found path; last with a default so existing constructors hold.
    entries: list[FastEntry] = field(default_factory=list)


def _relevance(item: ScoredPaper) -> float | None:
    """The best absolute match signal available for one hit, or None when unknowable.

    Cross-encoder relevance when reranking ran; cosine similarity otherwise; None for
    lexical-only hits (their sentinel distance measures nothing).
    """
    if item.relevance is not None:
        return item.relevance
    if item.distance >= _LEXICAL_SENTINEL:
        return None
    return 1.0 - item.distance


def _match_terms(question: str, item: ScoredPaper) -> list[str]:
    """Question terms that appear in the paper's title, abstract, or keywords."""
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    haystack = " ".join(
        [item.paper.title, item.paper.abstract, " ".join(item.paper.keywords or [])]
    ).lower()
    matched = []
    for token in dict.fromkeys(_TOKEN_RE.findall(question.lower())):
        if len(token) > 1 and token not in ENGLISH_STOP_WORDS and token in haystack:
            matched.append(token)
        if len(matched) == _FAST_MATCH_TERMS:
            break
    return matched


def _fast_text(entries: list[FastEntry]) -> str:
    """Render the deterministic extractive text from the structured entries."""
    plural = "s" if len(entries) != 1 else ""
    lines = [f"Found {len(entries)} recent paper{plural} matching your question."]
    for index, entry in enumerate(entries, start=1):
        head = f"{index}. {entry.title} [{entry.id}] - {entry.published_at.strftime('%b %Y')}"
        if entry.venue:
            head += f" - {entry.venue}"
        block = [head]
        if entry.matches:
            block.append("   Matches: " + ", ".join(entry.matches))
        if entry.keywords:
            block.append("   Keywords: " + ", ".join(entry.keywords))
        if entry.excerpt:
            block.append(f'   Excerpt: "{entry.excerpt}"')
        lines.append("\n".join(block))
    return "\n\n".join(lines)


def answer_fast(
    session: Session,
    embedder: Embedder,
    question: str,
    *,
    k: int = 8,
    days: int | None = None,
    timings: dict[str, float] | None = None,
) -> FastAnswer:
    """A deterministic extractive answer with no LLM call: papers, matches, excerpts.

    ``found`` is False when nothing clears the relevance floor — the router turns that
    into the not-found event that offers a web search. Cross-encoder scores and cosine
    similarities live on different scales, so the floor follows the evidence:
    ``RS_ASK_MIN_RELEVANCE`` when the reranker scored the hits, ``RS_ASK_MIN_SIMILARITY``
    when cosine did (rerank off, or skipped here via ``RS_ASK_FAST_RERANK=false``). When
    every hit's relevance is unknowable (lexical-only), emptiness is the only honest test.
    """
    with trace_span("ask-fast", question=question, k=k, days=days) as span:
        settings = get_settings()
        query_vector = embedder.embed_query(question)
        used = retrieve(
            session,
            embedder,
            question,
            k=k,
            days=days,
            use_rerank=settings.ask_fast_rerank,
            query_vector=query_vector,
            timings=timings,
        )
        span["retrieved"] = len(used)
        relevances = [_relevance(item) for item in used]
        known = [rel for rel in relevances if rel is not None]
        best = max(known) if known else None
        calibrated = any(item.relevance is not None for item in used)
        floor = settings.ask_min_relevance if calibrated else settings.ask_min_similarity
        found = (best is not None and best >= floor) or (not known and bool(used))
        span["found"] = found
        span["best_relevance"] = best
        if not found:
            return FastAnswer(
                answer=Answer(text=_NOT_FOUND_TEXT, cited=[], hallucinated=[], used=[]),
                found=False,
                best_relevance=best,
            )

        kept = [
            (item, rel)
            for item, rel in zip(used, relevances, strict=True)
            if rel is None or rel >= floor
        ][:_FAST_SHOWN]
        keep = [item for item, _ in kept]
        quotes = _excerpts_for(session, embedder, question, keep, query_vector)
        entries: list[FastEntry] = []
        for item, rel in kept:
            paper = item.paper
            quote = quotes.get(paper.id)
            entries.append(
                FastEntry(
                    id=paper.id,
                    title=paper.title,
                    published_at=paper.published_at,
                    venue=paper.venue,
                    matches=_match_terms(question, item),
                    keywords=(paper.keywords or [])[:6],
                    excerpt=quote[:_FAST_EXCERPT_CHARS] if quote else None,
                    relevance=rel,
                )
            )
        result = _post_check(_fast_text(entries), keep)
        span["cited"] = len(result.cited)
        return FastAnswer(answer=result, found=True, best_relevance=best, entries=entries)


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
    session: Session,
    embedder: Embedder,
    question: str,
    used: list[ScoredPaper],
    query_vector: list[float] | None = None,
) -> dict[str, str]:
    """Best-chunk excerpts when chunk retrieval is on (empty when off or nothing indexed).

    ``query_vector`` reuses the embed retrieval already paid for; without it (the agentic
    path) the question embeds once more here.
    """
    if not used or not get_settings().chunk_retrieval:
        return {}
    return best_chunk_texts(
        session,
        query_vector if query_vector is not None else embedder.embed_query(question),
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
    query_vector: list[float] | None = None,
) -> list[ScoredPaper]:
    """Agentic multi-hop retrieval when asked, otherwise the single-shot hybrid search."""
    if agentic:
        from researchscout.agentic import agentic_retrieve

        return agentic_retrieve(session, embedder, llm, question, k=k, days=days)
    return retrieve(session, embedder, question, k=k, days=days, query_vector=query_vector)


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
        query_vector = None if agentic else embedder.embed_query(question)
        used = _retrieve_for(
            session,
            embedder,
            llm,
            question,
            k=k,
            days=days,
            agentic=agentic,
            query_vector=query_vector,
        )
        span["retrieved"] = len(used)
        yield StreamMeta(retrieved=len(used), used=used)
        if not used:
            empty = "No recent papers match this question."
            yield StreamDelta(text=empty)
            yield Answer(text=empty, cited=[], hallucinated=[], used=[])
            return

        quotes = _excerpts_for(session, embedder, question, used, query_vector)
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
        query_vector = None if agentic else embedder.embed_query(question)
        used = _retrieve_for(
            session,
            embedder,
            llm,
            question,
            k=k,
            days=days,
            agentic=agentic,
            query_vector=query_vector,
        )
        span["retrieved"] = len(used)
        if not used:
            return Answer(
                text="No recent papers match this question.", cited=[], hallucinated=[], used=[]
            )

        quotes = _excerpts_for(session, embedder, question, used, query_vector)
        user_prompt = f"Question: {question}\n\nPapers:\n{_context(used, quotes)}"
        text = llm.complete(_SYSTEM_PROMPT, user_prompt)
        span["model"] = llm.model

        result = _post_check(text, used)
        span["cited"] = len(result.cited)
        span["hallucinated"] = len(result.hallucinated)
        return result
