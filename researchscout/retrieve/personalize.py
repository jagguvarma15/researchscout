"""Personalized "For You" ranking: reweight the window's papers toward a reader's interests.

A reader's stored interests become one embedding centroid; each recent paper is scored by its cosine
similarity to that centroid, then reweighted by the same recency and breakthrough factors the global
search uses. With no interests there is nothing to personalize, so the caller falls back to the
global feed.
"""

from __future__ import annotations

import math

from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.retrieve.search import _DEFAULT_HALF_LIFE_DAYS, ScoredPaper, _recency_weight
from researchscout.score import breakthrough
from researchscout.store.papers import get_paper
from researchscout.store.topics import window_vectors


def interest_centroid(embedder: Embedder, interests: list[str]) -> list[float] | None:
    """Average the interest embeddings into one unit vector; None when there are no interests."""
    cleaned = [interest.strip() for interest in interests if interest.strip()]
    if not cleaned:
        return None
    vectors = [embedder.embed_query(interest) for interest in cleaned]
    dim = len(vectors[0])
    mean = [sum(vector[i] for vector in vectors) / len(vectors) for i in range(dim)]
    norm = math.sqrt(sum(value * value for value in mean))
    return [value / norm for value in mean] if norm > 0 else None


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def personalized_papers(
    session: Session,
    embedder: Embedder,
    interests: list[str],
    *,
    k: int = 20,
    days: int,
    half_life_days: float = _DEFAULT_HALF_LIFE_DAYS,
) -> list[ScoredPaper]:
    """Window papers by interest similarity x recency x breakthrough; empty on cold start."""
    centroid = interest_centroid(embedder, interests)
    if centroid is None:
        return []
    scored: list[ScoredPaper] = []
    for paper_id, _title, vector in window_vectors(session, days=days, model_id=embedder.model_id):
        paper = get_paper(session, paper_id)
        if paper is None:
            continue
        similarity = max(_cosine(centroid, vector), 0.0)
        score = (
            similarity
            * _recency_weight(paper.published_at, half_life_days)
            * (1.0 + breakthrough(session, paper_id).total)
        )
        scored.append(ScoredPaper(paper=paper, score=score, distance=1.0 - similarity))
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:k]
