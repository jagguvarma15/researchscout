"""Grounded, cited answers: retrieve within the freshness window, then synthesize citing only those.

The synthesis is constrained to the retrieved papers, and a post-check drops any cited id the model
invented (a hallucinated citation never survives into ``Answer.cited``).
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from researchscout.config import get_settings
from researchscout.embed.base import Embedder
from researchscout.llm.base import LLM
from researchscout.llm.tracing import NOOP_RUN, PipelineRun
from researchscout.llm.usage import PURPOSE_SYNTHESIS, last_usage, llm_purpose
from researchscout.retrieve.search import ScoredPaper, retrieve
from researchscout.store.chunks import best_chunk_texts
from researchscout.store.facets import PaperFacets
from researchscout.trace import trace_span

# One prior exchange turn as (role, text), role "user" or "assistant". A plain tuple rather
# than the API's ChatTurn model: schemas.py imports from this module, so the dependency can
# only point that way.
Turn = tuple[str, str]

# How much conversation reaches the prompt: the last few turns, each clipped, so history
# never crowds the papers out of the context window.
_HISTORY_TURNS = 6
_HISTORY_TURN_CHARS = 500
# A follow-up this short ("what about video?") rarely stands alone; retrieval borrows the
# previous user turn. Deterministic on purpose - a rewrite model call would double the cost
# of every chat message against a small daily quota.
_SHORT_QUESTION_WORDS = 6
# A hand-pinned paper's age is irrelevant, so the pin lifts the freshness window.
_PINNED_WINDOW_DAYS = 3650


def _retrieval_query(question: str, history: list[Turn] | None) -> str:
    """The text retrieval runs on: short follow-ups borrow the previous user turn."""
    if history and len(question.split()) < _SHORT_QUESTION_WORDS:
        last_user = next((text for role, text in reversed(history) if role == "user"), None)
        if last_user:
            return f"{last_user} {question}"
    return question


def _history_block(history: list[Turn] | None) -> str:
    """The conversation-so-far prompt block, or empty without history."""
    if not history:
        return ""
    lines = [
        f"{'Reader' if role == 'user' else 'Scout'}: {text[:_HISTORY_TURN_CHARS]}"
        for role, text in history[-_HISTORY_TURNS:]
    ]
    return "Conversation so far:\n" + "\n".join(lines) + "\n\n"


def _pin_facets(paper_id: str | None, days: int | None) -> PaperFacets | None:
    """Facets scoping retrieval to one paper, or None when nothing is pinned."""
    if paper_id is None:
        return None
    return PaperFacets(days=days if days is not None else _PINNED_WINDOW_DAYS, only=[paper_id])


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
    # What produced the answer, when the client recorded it: the model id and token
    # counts land here right after the generation finishes (same resumption window as the
    # usage contextvar - see llm/usage.py). None with fakes and on empty-retrieval paths.
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    # The agentic sub-questions, when decomposition produced more than one.
    plan: list[str] | None = None


@dataclass
class StreamPlan:
    """Agentic only, before ``StreamMeta``: the sub-questions retrieval will run."""

    parts: list[str]


@dataclass
class StreamMeta:
    """What retrieval found, before any generation (after ``StreamPlan`` when agentic)."""

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
    # Empty on the not-found path; defaulted so existing constructors hold.
    entries: list[FastEntry] = field(default_factory=list)
    # What retrieval found BEFORE the relevance floor. The not-found path strips
    # ``answer.used``, so without this the metrics read "retrieved nothing" for exactly
    # the questions the corpus-gap panel exists to analyze.
    retrieved: int = 0


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
    paper_id: str | None = None,
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
            facets=_pin_facets(paper_id, days),
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
        if paper_id is not None:
            # A pinned ask cannot miss: the reader chose the paper, so relevance to it is
            # their assertion rather than the gate's. Questions with pronouns ("what does
            # this introduce?") score low against any abstract, and an excerpt from the
            # right paper beats a refusal to look at it.
            found = bool(used)
        span["found"] = found
        span["best_relevance"] = best
        if not found:
            return FastAnswer(
                answer=Answer(text=_NOT_FOUND_TEXT, cited=[], hallucinated=[], used=[]),
                found=False,
                best_relevance=best,
                retrieved=len(used),
            )

        kept = [
            (item, rel)
            for item, rel in zip(used, relevances, strict=True)
            if paper_id is not None or rel is None or rel >= floor
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
        return FastAnswer(
            answer=result,
            found=True,
            best_relevance=best,
            entries=entries,
            retrieved=len(used),
        )


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
    paper_id: str | None = None,
    query_vector: list[float] | None = None,
    timings: dict[str, float] | None = None,
    parts: list[str] | None = None,
    run: PipelineRun | None = None,
) -> list[ScoredPaper]:
    """Agentic multi-hop retrieval when asked, otherwise the single-shot hybrid search.

    A paper pin overrides agentic: decomposing a question about one hand-chosen paper into
    sub-searches would wander off the pin, and the pin already is the retrieval answer.
    ``parts`` carries an already-shown decomposition into the agentic path; ``timings``
    reaches only the single-shot leg (the agentic path is timed as a whole by the caller).
    """
    if agentic and paper_id is None:
        from researchscout.agentic import agentic_retrieve

        return agentic_retrieve(
            session, embedder, llm, question, k=k, days=days, parts=parts, run=run
        )
    return retrieve(
        session,
        embedder,
        question,
        k=k,
        days=days,
        facets=_pin_facets(paper_id, days),
        query_vector=query_vector,
        timings=timings,
    )


def _plan_for(
    llm: LLM, search_text: str, *, agentic: bool, paper_id: str | None, run: PipelineRun
) -> list[str] | None:
    """The agentic decomposition, or None when the ask is single-shot (or pinned)."""
    if not agentic or paper_id is not None:
        return None
    from researchscout.agentic import decompose

    with run.step("decompose", inputs={"question": search_text}) as step, step.ambient():
        parts = decompose(llm, search_text)
        step.out(parts=parts)
    return parts


def _attach_usage(result: Answer, llm: LLM, plan: list[str] | None) -> Answer:
    """Stamp the answer with its model, token cost, and plan.

    Must run in the same resumption window that finished the generation - the usage
    contextvar does not survive the SSE threadpool's per-call context copies.
    """
    result.model = llm.model
    result.plan = plan if plan and len(plan) > 1 else None
    usage = last_usage()
    # Both guards matter: the purpose keeps a decompose or guardrail record out, and the
    # model identity keeps a non-recording LLM (a test fake) from inheriting a stale
    # record left in this context by an earlier real call.
    if usage is not None and usage.purpose == PURPOSE_SYNTHESIS and usage.model == llm.model:
        result.prompt_tokens = usage.prompt_tokens
        result.completion_tokens = usage.completion_tokens
    return result


def answer_stream(
    session: Session,
    embedder: Embedder,
    llm: LLM,
    question: str,
    *,
    k: int = 8,
    days: int | None = None,
    agentic: bool = False,
    history: list[Turn] | None = None,
    paper_id: str | None = None,
    timings: dict[str, float] | None = None,
    trace: PipelineRun | None = None,
) -> Iterator[StreamPlan | StreamMeta | StreamDelta | Answer]:
    """Streaming variant of :func:`answer`: plan (agentic), meta, deltas, the final Answer.

    The final :class:`Answer` carries the citation post-check over the accumulated text — the
    same guarantee as the non-streaming path, it just arrives after the last delta. A
    ``timings`` dict, when given, is filled with retrieve_ms (decompose included when
    agentic), rerank_ms (single-shot only), and llm_ms; ``trace`` attaches the stages to a
    pipeline run.
    """
    run = trace if trace is not None else NOOP_RUN
    with trace_span(
        "ask", question=question, k=k, days=days, streaming=True, agentic=agentic
    ) as span:
        search_text = _retrieval_query(question, history)
        retrieval_started = time.perf_counter()
        plan = _plan_for(llm, search_text, agentic=agentic, paper_id=paper_id, run=run)
        if plan is not None and len(plan) > 1:
            yield StreamPlan(parts=plan)
        query_vector = None if plan is not None else embedder.embed_query(search_text)
        inner: dict[str, float] = {}
        used = _retrieve_for(
            session,
            embedder,
            llm,
            search_text,
            k=k,
            days=days,
            agentic=agentic,
            paper_id=paper_id,
            query_vector=query_vector,
            timings=inner,
            parts=plan,
            run=run,
        )
        if timings is not None:
            timings["retrieve_ms"] = round((time.perf_counter() - retrieval_started) * 1000.0, 1)
            if "rerank_ms" in inner:
                timings["rerank_ms"] = inner["rerank_ms"]
        span["retrieved"] = len(used)
        yield StreamMeta(retrieved=len(used), used=used)
        if not used:
            empty = "No recent papers match this question."
            yield StreamDelta(text=empty)
            yield Answer(text=empty, cited=[], hallucinated=[], used=[])
            return

        quotes = _excerpts_for(session, embedder, question, used, query_vector)
        user_prompt = (
            f"{_history_block(history)}Question: {question}\n\nPapers:\n{_context(used, quotes)}"
        )
        parts: list[str] = []
        llm_started = time.perf_counter()
        # The purpose and ambient blocks cover only the call: the client reads them
        # eagerly, and a block spanning the loop would die with the SSE threadpool's
        # context copies.
        with run.step("synthesize") as step, step.ambient(), llm_purpose(PURPOSE_SYNTHESIS):
            deltas = llm.stream(_SYSTEM_PROMPT, user_prompt)
        for delta in deltas:
            parts.append(delta)
            yield StreamDelta(text=delta)
        if timings is not None:
            timings["llm_ms"] = round((time.perf_counter() - llm_started) * 1000.0, 1)
        span["model"] = llm.model

        result = _attach_usage(_post_check("".join(parts), used), llm, plan)
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
    history: list[Turn] | None = None,
    paper_id: str | None = None,
    timings: dict[str, float] | None = None,
    trace: PipelineRun | None = None,
) -> Answer:
    """Retrieve recent papers and synthesize a grounded, cited answer."""
    run = trace if trace is not None else NOOP_RUN
    with trace_span("ask", question=question, k=k, days=days, agentic=agentic) as span:
        search_text = _retrieval_query(question, history)
        retrieval_started = time.perf_counter()
        plan = _plan_for(llm, search_text, agentic=agentic, paper_id=paper_id, run=run)
        query_vector = None if plan is not None else embedder.embed_query(search_text)
        inner: dict[str, float] = {}
        used = _retrieve_for(
            session,
            embedder,
            llm,
            search_text,
            k=k,
            days=days,
            agentic=agentic,
            paper_id=paper_id,
            query_vector=query_vector,
            timings=inner,
            parts=plan,
            run=run,
        )
        if timings is not None:
            timings["retrieve_ms"] = round((time.perf_counter() - retrieval_started) * 1000.0, 1)
            if "rerank_ms" in inner:
                timings["rerank_ms"] = inner["rerank_ms"]
        span["retrieved"] = len(used)
        if not used:
            return Answer(
                text="No recent papers match this question.", cited=[], hallucinated=[], used=[]
            )

        quotes = _excerpts_for(session, embedder, question, used, query_vector)
        user_prompt = (
            f"{_history_block(history)}Question: {question}\n\nPapers:\n{_context(used, quotes)}"
        )
        llm_started = time.perf_counter()
        with run.step("synthesize") as step, step.ambient(), llm_purpose(PURPOSE_SYNTHESIS):
            text = llm.complete(_SYSTEM_PROMPT, user_prompt)
        if timings is not None:
            timings["llm_ms"] = round((time.perf_counter() - llm_started) * 1000.0, 1)
        span["model"] = llm.model

        result = _attach_usage(_post_check(text, used), llm, plan)
        span["cited"] = len(result.cited)
        span["hallucinated"] = len(result.hallucinated)
        return result
