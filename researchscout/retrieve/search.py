"""Freshness-aware retrieval: embed the query, ANN over pgvector with filters, recency-weight, rank.

Freshness is a correctness property here: the date window is a hard filter, not a soft penalty — a
paper outside the window is never returned. Within the window, results are re-ranked by semantic
similarity times a recency weight, so a slightly-less-similar but much newer paper can outrank an
older one. We over-fetch by similarity first so the recency re-rank has candidates to promote.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, cast
from sqlalchemy.dialects.postgresql import ARRAY, TEXT
from sqlalchemy.orm import Session

from researchscout.config import get_settings
from researchscout.embed.base import Embedder
from researchscout.schema import Paper
from researchscout.store.models import PaperRow
from researchscout.store.papers import get_paper
from researchscout.store.vectors import search as vector_search

_DEFAULT_HALF_LIFE_DAYS = 14.0
_OVERFETCH = 4


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
    """Return up to ``k`` papers within the freshness window, ranked by similarity x recency."""
    window_days = days if days is not None else get_settings().freshness_days
    query_vector = embedder.embed_query(query)
    where = _filters(window_days, categories)
    candidates = vector_search(session, query_vector, k=k * _OVERFETCH, where=where)

    scored: list[ScoredPaper] = []
    for paper_id, distance in candidates:
        paper = get_paper(session, paper_id)
        if paper is None:
            continue
        similarity = 1.0 - distance
        score = similarity * _recency_weight(paper.published_at, half_life_days)
        scored.append(ScoredPaper(paper=paper, score=score, distance=distance))

    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:k]
