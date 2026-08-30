"""Agentic multi-hop retrieval: decompose the question, retrieve per part, follow references.

One embedding of a broad question misses papers matching only one facet. This asks the LLM to
split it into focused sub-questions, retrieves each, fuses the hits with RRF (a paper surfaced
by several sub-questions floats up), and (best-effort) follows one hop of Semantic Scholar
references to pull in cited work already in the store. The union feeds the same
grounded-citation synthesis, which still cites only what it was handed.

Per-request opt-in: the API runs it when a request asks (``agentic=true``, the web's /deep
command); ``RS_AGENTIC_ASK`` sets only the CLI default. It costs an extra LLM call and one
retrieval per sub-question.
"""

from __future__ import annotations

import logging
import re
import time

from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.llm.base import LLM
from researchscout.llm.tracing import NOOP_RUN, PipelineRun
from researchscout.llm.usage import PURPOSE_DECOMPOSE, llm_purpose
from researchscout.retrieve.search import ScoredPaper, retrieve
from researchscout.schema import normalize_arxiv_id
from researchscout.store.facets import PaperFacets
from researchscout.useragent import default_headers

logger = logging.getLogger(__name__)

_DECOMPOSE_SYSTEM = (
    "Break the user's research question into 2-4 focused sub-questions, one per line, with no "
    "numbering or preamble. Each should target a distinct facet worth searching on its own. If the "
    "question is already atomic, return it unchanged on a single line."
)
_S2_BASE = "https://api.semanticscholar.org/graph/v1"
# Short on purpose: the hop runs inside the request (and the held generation slot), and a
# slow upstream must cost seconds, not half a minute per source.
_REQUEST_TIMEOUT = 8.0
# Total wall-clock the hop may spend on uncached fetches; cached edges always merge.
_HOP_BUDGET_SEC = 10.0
# Strips a leading list marker (1., 2), -, *, •) but not real leading digits.
_MARKER_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
# Same constant as retrieve.search's leg fusion, applied here across sub-questions.
_RRF_K = 60


def decompose(llm: LLM, question: str, *, max_parts: int = 4) -> list[str]:
    """Split a question into focused sub-questions; the question itself is the floor.

    Any model failure falls back to single-shot retrieval — a deep ask degrades to a
    normal one, never to an error.
    """
    try:
        with llm_purpose(PURPOSE_DECOMPOSE):
            reply = llm.complete(_DECOMPOSE_SYSTEM, question)
    except Exception:  # noqa: BLE001 - single-shot retrieval is the safe floor
        logger.warning("decompose failed; falling back to single-shot retrieval", exc_info=True)
        return [question]
    seen: set[str] = set()
    parts: list[str] = []
    for line in reply.splitlines():
        part = _MARKER_RE.sub("", line).strip()
        key = part.lower()
        if part and key not in seen:
            seen.add(key)
            parts.append(part)
    return parts[:max_parts] if parts else [question]


def _fuse(results: list[list[ScoredPaper]]) -> list[ScoredPaper]:
    """RRF across sub-question lists, kept on the single-shot score scale.

    Ranks (not scores) fuse across lists, so sub-retrievals need no calibration. The final
    score keeps each paper's recency-and-breakthrough prior instead of discarding it: with
    a cross-encoder relevance it is ``relevance x prior x (1 + rrf)`` — the single-shot
    reranked formula with a small cross-part consensus multiplier (at most ~1.07 for four
    parts) — and without one it is ``rrf x prior``, the single-shot fusion formula with
    sub-questions standing in for legs. Either way fused scores stay comparable to the
    non-agentic path, and fresh or high-momentum papers keep their boost.

    The representative appearance is the closest by distance; relevance is the best seen
    across appearances (under reranking those can be different items).
    """
    rrf: dict[str, float] = {}
    keep: dict[str, ScoredPaper] = {}
    best_relevance: dict[str, float] = {}
    for hits in results:
        for rank, item in enumerate(hits, start=1):
            paper_id = item.paper.id
            rrf[paper_id] = rrf.get(paper_id, 0.0) + 1.0 / (_RRF_K + rank)
            current = keep.get(paper_id)
            if current is None or item.distance < current.distance:
                keep[paper_id] = item
            if item.relevance is not None and item.relevance > best_relevance.get(paper_id, -1.0):
                best_relevance[paper_id] = item.relevance
    rescored = []
    for paper_id, rrf_sum in rrf.items():
        representative = keep[paper_id]
        relevance = best_relevance.get(paper_id)
        prior = representative.prior
        if relevance is not None:
            score = relevance * prior * (1.0 + rrf_sum)
        else:
            score = rrf_sum * prior
        rescored.append(
            ScoredPaper(
                paper=representative.paper,
                score=score,
                distance=representative.distance,
                relevance=relevance,
                prior=prior,
            )
        )
    return sorted(rescored, key=lambda item: item.score, reverse=True)


def _merge(results: list[list[ScoredPaper]]) -> list[ScoredPaper]:
    """Union lists, deduped by paper id keeping the highest score (used for the reference hop)."""
    best: dict[str, ScoredPaper] = {}
    for hits in results:
        for item in hits:
            current = best.get(item.paper.id)
            if current is None or item.score > current.score:
                best[item.paper.id] = item
    return sorted(best.values(), key=lambda item: item.score, reverse=True)


def _reference_arxiv_ids(arxiv_id: str, *, limit: int) -> list[str] | None:
    """The arXiv ids this paper references via Semantic Scholar, or None on any failure.

    None (not an empty list) on failure, so a transient error is never cached as "no
    references". The catch is deliberately broad: a payload shape change upstream must
    degrade the hop, never crash the ask.
    """
    import httpx

    try:
        resp = httpx.get(
            f"{_S2_BASE}/paper/arXiv:{arxiv_id}/references",
            params={"fields": "externalIds", "limit": limit},
            headers=default_headers(),
            timeout=_REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        payload = resp.json()
        ids: list[str] = []
        for entry in payload.get("data", []):
            external = (entry.get("citedPaper") or {}).get("externalIds") or {}
            arxiv = external.get("ArXiv")
            if arxiv:
                ids.append(normalize_arxiv_id(str(arxiv)))
    except Exception:  # noqa: BLE001 - the hop is best-effort context, not a dependency
        logger.warning("reference fetch failed for %s", arxiv_id, exc_info=True)
        return None
    return ids


def follow_references(
    session: Session, papers: list[ScoredPaper], *, max_sources: int = 5, per_source: int = 10
) -> list[ScoredPaper]:
    """One hop of citation-following: referenced papers already in the store, score 0 (context).

    Cache-first: each source paper's references are fetched from Semantic Scholar at most once
    and persisted as citation edges, so repeat questions cost no HTTP and the edges accrue into
    a local citation graph. Uncached fetches share a wall-clock budget — once it is spent the
    remaining sources contribute only their cached edges.
    """
    from researchscout.store.citations import references_cached, store_references
    from researchscout.store.papers import find_by_external_id, get_paper

    started = time.monotonic()
    seen = {item.paper.id for item in papers}
    added: dict[str, ScoredPaper] = {}
    for item in papers[:max_sources]:
        arxiv = item.paper.external_ids.get("arxiv")
        if not arxiv:
            continue
        refs = references_cached(session, item.paper.id)
        if refs is None:
            if time.monotonic() - started > _HOP_BUDGET_SEC:
                continue
            fetched = _reference_arxiv_ids(arxiv, limit=per_source)
            if fetched is None:
                continue
            store_references(session, item.paper.id, fetched)
            refs = fetched
        for ref_arxiv in refs:
            canonical = find_by_external_id(session, "arxiv", ref_arxiv)
            if canonical is None or canonical in seen or canonical in added:
                continue
            paper = get_paper(session, canonical)
            if paper is not None:
                added[canonical] = ScoredPaper(paper=paper, score=0.0, distance=1.0)
    return list(added.values())


def agentic_retrieve(
    session: Session,
    embedder: Embedder,
    llm: LLM,
    question: str,
    *,
    k: int = 8,
    days: int | None = None,
    facets: PaperFacets | None = None,
    follow_citations: bool = True,
    parts: list[str] | None = None,
    run: PipelineRun | None = None,
) -> list[ScoredPaper]:
    """Decompose, retrieve per sub-question, fuse by RRF, follow one hop, and take the top-k.

    ``facets`` applies to every sub-retrieval, so agentic mode filters exactly like the
    single-shot path. A caller that already decomposed (to show the plan) passes ``parts``;
    ``run`` attaches each stage to a pipeline trace. The reference hop appends context
    papers at score 0 via ``_merge`` (they never displace a retrieved hit).
    """
    trace = run if run is not None else NOOP_RUN
    if parts is None:
        with trace.step("decompose", inputs={"question": question}) as step, step.ambient():
            parts = decompose(llm, question)
            step.out(parts=parts)
    results = []
    for part in parts:
        with trace.step("retrieve", inputs={"part": part}) as step:
            hits = retrieve(session, embedder, part, k=k, days=days, facets=facets)
            step.out(retrieved=len(hits))
        results.append(hits)
    merged = _fuse(results)
    if follow_citations and merged:
        with trace.step("follow-references") as step:
            extra = follow_references(session, merged)
            step.out(added=len(extra))
        merged = _merge([merged, extra])
    return merged[:k]
