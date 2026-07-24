"""Agentic multi-hop retrieval: decompose the question, retrieve per part, follow references.

One embedding of a broad question misses papers matching only one facet. This asks the LLM to
split it into focused sub-questions, retrieves each, merges the hits, and (best-effort) follows
one hop of Semantic Scholar references to pull in cited work already in the store. The union
feeds the same grounded-citation synthesis, which still cites only what it was handed.

Off by default (``RS_AGENTIC_ASK``): it costs an extra LLM call and one retrieval per sub-question.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.llm.base import LLM
from researchscout.retrieve.search import ScoredPaper, retrieve
from researchscout.schema import normalize_arxiv_id

_DECOMPOSE_SYSTEM = (
    "Break the user's research question into 2-4 focused sub-questions, one per line, with no "
    "numbering or preamble. Each should target a distinct facet worth searching on its own. If the "
    "question is already atomic, return it unchanged on a single line."
)
_S2_BASE = "https://api.semanticscholar.org/graph/v1"
_REQUEST_TIMEOUT = 30.0
# Strips a leading list marker (1., 2), -, *, •) but not real leading digits.
_MARKER_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def decompose(llm: LLM, question: str, *, max_parts: int = 4) -> list[str]:
    """Split a question into focused sub-questions; fall back to the question itself."""
    seen: set[str] = set()
    parts: list[str] = []
    for line in llm.complete(_DECOMPOSE_SYSTEM, question).splitlines():
        part = _MARKER_RE.sub("", line).strip()
        key = part.lower()
        if part and key not in seen:
            seen.add(key)
            parts.append(part)
    return parts[:max_parts] if parts else [question]


def _merge(results: list[list[ScoredPaper]]) -> list[ScoredPaper]:
    """Union hits across sub-questions, deduped by paper id keeping the highest score."""
    best: dict[str, ScoredPaper] = {}
    for hits in results:
        for item in hits:
            current = best.get(item.paper.id)
            if current is None or item.score > current.score:
                best[item.paper.id] = item
    return sorted(best.values(), key=lambda item: item.score, reverse=True)


def _reference_arxiv_ids(arxiv_id: str, *, limit: int) -> list[str]:
    """Best-effort: the arXiv ids this paper references, via Semantic Scholar."""
    import httpx

    try:
        resp = httpx.get(
            f"{_S2_BASE}/paper/arXiv:{arxiv_id}/references",
            params={"fields": "externalIds", "limit": limit},
            timeout=_REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError):
        return []
    ids: list[str] = []
    for entry in payload.get("data", []):
        external = (entry.get("citedPaper") or {}).get("externalIds") or {}
        arxiv = external.get("ArXiv")
        if arxiv:
            ids.append(normalize_arxiv_id(str(arxiv)))
    return ids


def follow_references(
    session: Session, papers: list[ScoredPaper], *, max_sources: int = 5, per_source: int = 10
) -> list[ScoredPaper]:
    """One hop of citation-following: referenced papers already in the store, score 0 (context)."""
    from researchscout.store.papers import find_by_external_id, get_paper

    seen = {item.paper.id for item in papers}
    added: dict[str, ScoredPaper] = {}
    for item in papers[:max_sources]:
        arxiv = item.paper.external_ids.get("arxiv")
        if not arxiv:
            continue
        for ref_arxiv in _reference_arxiv_ids(arxiv, limit=per_source):
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
    follow_citations: bool = True,
) -> list[ScoredPaper]:
    """Decompose, retrieve per sub-question, merge, follow one hop, and take the top-k."""
    parts = decompose(llm, question)
    merged = _merge([retrieve(session, embedder, part, k=k, days=days) for part in parts])
    if follow_citations and merged:
        merged = _merge([merged, follow_references(session, merged)])
    return merged[:k]
