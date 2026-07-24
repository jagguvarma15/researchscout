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

If the query yields no tsquery lexemes (stopwords only) or the lexical leg errors, retrieval
degrades gracefully to vector-only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, cast
from sqlalchemy.dialects.postgresql import ARRAY, TEXT
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from researchscout.config import get_settings
from researchscout.embed.base import Embedder
from researchscout.schema import Paper
from researchscout.score import breakthrough
from researchscout.store.lexical import lexical_search
from researchscout.store.models import PaperRow
from researchscout.store.papers import get_paper
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


def _filters(days: int, categories: list[str] | None) -> ColumnElement[bool]:
    window_start = datetime.now(UTC) - timedelta(days=days)
    clause: ColumnElement[bool] = PaperRow.published_at >= window_start
    if categories:
        clause = clause & PaperRow.categories.op("?|")(cast(categories, ARRAY(TEXT)))
    return clause


def retrieve(
    session: Session,
    embedder: Embedder,
    query: str,
    *,
    k: int = 10,
    days: int | None = None,
    categories: list[str] | None = None,
    half_life_days: float = _DEFAULT_HALF_LIFE_DAYS,
) -> list[ScoredPaper]:
    """Up to ``k`` in-window papers, ranked by RRF(vector, lexical) x recency x authority."""
    window_days = days if days is not None else get_settings().freshness_days
    where = _filters(window_days, categories)

    query_vector = embedder.embed_query(query)
    vector_hits = vector_search(session, query_vector, k=_LEG_K, where=where)
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
    scored: list[ScoredPaper] = []
    for paper_id, rrf_score in fused.items():
        paper = get_paper(session, paper_id)
        if paper is None:
            continue
        boost = breakthrough(session, paper_id).total
        score = rrf_score * _recency_weight(paper.published_at, half_life_days) * (1.0 + boost)
        # Lexical-only hits have no measured cosine distance; report the maximum.
        scored.append(ScoredPaper(paper=paper, score=score, distance=distances.get(paper_id, 1.0)))

    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:k]
