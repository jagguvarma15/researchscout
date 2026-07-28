"""Authority-weighted hybrid retrieval: vector + lexical legs fused by RRF, freshness-aware.

The algorithm, in order:

1. **Hard freshness filter** — the date window is a correctness property, not a soft penalty; a
   paper outside the window is never returned. Category filters apply to both legs the same way.
2. **Two retrieval legs** over the filtered pool: pgvector cosine ANN on the query embedding, and
   Postgres full-text search (``websearch_to_tsquery`` + ``ts_rank_cd`` over the generated,
   title-weighted ``search_tsv`` column). Each leg contributes its top ``_LEG_K`` candidates.
3. **Reciprocal Rank Fusion** — a candidate's fused score is ``sum(1 / (60 + rank))`` across the
   legs it appears in (rank is 1-based). RRF needs no score calibration between cosine distance
   and ts_rank, and papers found by both legs float upward.
4. **Recency and breakthrough reweighting** — the fused score is multiplied by the exponential
   recency weight and by ``1 + breakthrough_score``, a momentum-aware boost over the paper's signal
   series (citations, trending rank, code stars). With no signals the boost is 0, so ranking falls
   back to recency; with only static citations it reduces to ``1 + log1p(citations)`` — the prior
   behaviour, recovered as a special case.
5. **Optional cross-encoder rerank** — when ``RS_RERANK_ENABLED`` is set, the top first-stage
   candidates are re-scored by a cross encoder that reads the query and paper together; its
   relevance replaces the RRF term while the recency-and-breakthrough prior is kept. Off by
   default, so the four steps above are the standard path.

If the query yields no tsquery lexemes (stopwords only) or the lexical leg errors, retrieval
degrades gracefully to vector-only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from researchscout.config import get_settings
from researchscout.embed.base import Embedder
from researchscout.rerank import Candidate, get_reranker, rerank
from researchscout.schema import Paper
from researchscout.score import breakthrough_many
from researchscout.store.facets import PaperFacets, facets_where
from researchscout.store.lexical import lexical_search
from researchscout.store.papers import get_papers
from researchscout.store.vectors import search as vector_search

_DEFAULT_HALF_LIFE_DAYS = 14.0
_LEG_K = 40
_RRF_K = 60


@dataclass
class ScoredPaper:
    paper: Paper
    score: float
    distance: float


def _recency_weight(published_at: datetime, half_life_days: float) -> float:
    age_days = max((datetime.now(UTC) - published_at).total_seconds() / 86400.0, 0.0)
    return math.exp(-age_days / half_life_days)


def retrieve(
    session: Session,
    embedder: Embedder,
    query: str,
    *,
    k: int = 10,
    days: int | None = None,
    categories: list[str] | None = None,
    facets: PaperFacets | None = None,
    half_life_days: float = _DEFAULT_HALF_LIFE_DAYS,
) -> list[ScoredPaper]:
    """Up to ``k`` in-window papers, ranked by RRF x recency x breakthrough, optionally reranked.

    ``facets`` filters both retrieval legs; the legacy ``days``/``categories`` kwargs fold into
    it. The hard freshness window applies unless the facets already bound time (a year/month
    window replaces it).
    """
    settings = get_settings()
    if facets is None:
        facets = PaperFacets(days=days, categories=categories)
    if facets.days is None and facets.year is None:
        facets = replace(facets, days=settings.freshness_days)
    where = facets_where(facets)

    query_vector = embedder.embed_query(query)
    vector_hits = vector_search(
        session, query_vector, model_id=embedder.model_id, k=_LEG_K, where=where
    )
    try:
        lexical_hits = lexical_search(session, query, k=_LEG_K, where=where)
    except SQLAlchemyError:
        session.rollback()
        lexical_hits = []

    fused: dict[str, float] = {}
    for hits in (vector_hits, lexical_hits):
        for rank, (paper_id, _leg_score) in enumerate(hits, start=1):
            fused[paper_id] = fused.get(paper_id, 0.0) + 1.0 / (_RRF_K + rank)

    distances = dict(vector_hits)
    # Hydrate every fused candidate in three grouped queries, not one round-trip per paper.
    papers = get_papers(session, list(fused))
    boosts = breakthrough_many(session, list(papers))
    candidates: list[Candidate] = []
    lookup: dict[str, tuple[Paper, float]] = {}
    for paper_id, rrf_score in fused.items():
        paper = papers.get(paper_id)
        if paper is None:
            continue
        prior = _recency_weight(paper.published_at, half_life_days) * (1.0 + boosts[paper_id].total)
        candidates.append(
            Candidate(
                key=paper_id,
                text=f"{paper.title}\n\n{paper.abstract}",
                prior=prior,
                first_stage=rrf_score * prior,
            )
        )
        # Lexical-only hits have no measured cosine distance; report the maximum.
        lookup[paper_id] = (paper, distances.get(paper_id, 1.0))

    ranked = rerank(query, candidates, get_reranker(), top_n=max(settings.rerank_top_n, k))
    return [
        ScoredPaper(paper=lookup[key][0], score=score, distance=lookup[key][1])
        for key, score in ranked
    ][:k]
