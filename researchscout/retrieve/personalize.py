"""Personalized "For You" ranking: reweight the window's papers toward a reader's profile.

The default path is v1: stored interest keywords average into one centroid and each recent
paper scores by cosine to it, reweighted by recency and breakthrough. ``RS_FORYOU_CENTROIDS``
switches on v2: saved papers (weighted down by save age) and interest keywords cluster into up
to K centroids, a paper scores by its best-matching centroid, and that centroid's anchor names
the reason shown in the feed ("Close to your saved paper: ..."). Two optional passes follow:
MMR trades relevance for diversity (``RS_FORYOU_MMR_LAMBDA`` below 1), and exploration slots
(``RS_FORYOU_EXPLORE_SLOTS``) surface high-momentum papers outside every centroid so the feed
never closes into a bubble. With no profile there is nothing to personalize, so the caller
falls back to the global feed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from researchscout.config import get_settings
from researchscout.embed.base import Embedder
from researchscout.retrieve.search import _DEFAULT_HALF_LIFE_DAYS, _recency_weight
from researchscout.schema import Paper
from researchscout.score import breakthrough_many
from researchscout.store.account import dismissed_papers
from researchscout.store.events import dismissed_event_paper_ids, positive_event_vectors
from researchscout.store.papers import get_papers
from researchscout.store.saved import saved_vectors
from researchscout.store.topics import window_vectors

# A paper this dissimilar to every centroid counts as "outside" the profile for exploration.
_EXPLORE_SIMILARITY_CEILING = 0.5
_EXPLORE_REASON = "Rising outside your usual topics"

# What a click/dwell/open is worth relative to a save. Opening a paper is curiosity, saving
# it is a commitment; the profile should lean on the stronger statement.
_EVENT_WEIGHT = 0.3


@dataclass(frozen=True)
class InterestCluster:
    centroid: list[float]
    reason: str | None


@dataclass
class PersonalizedPaper:
    paper: Paper
    score: float
    distance: float
    reason: str | None


@dataclass
class _Entry:
    paper: Paper
    vector: list[float]
    similarity: float
    score: float
    reason: str | None


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


def profile_clusters(
    session: Session,
    embedder: Embedder,
    user_sub: str,
    interests: list[str],
    *,
    k: int,
    half_life_days: float,
) -> list[InterestCluster]:
    """Time-decayed saved papers plus interest keywords, clustered into up to ``k`` centroids.

    Every centroid carries the reason of its heaviest member — the saved paper, opened paper
    or interest keyword that anchors it — which is what the feed shows as "why this paper".
    With ``RS_FORYOU_EVENTS`` on, papers the reader recently clicked, dwelled on or opened
    join the profile at a fraction of a save's weight, decayed on the same half-life.
    """
    now = datetime.now(UTC)
    weighted: list[tuple[list[float], float, str]] = []
    for _paper_id, title, saved_at, vector in saved_vectors(session, user_sub, embedder.model_id):
        age_days = max((now - saved_at).total_seconds() / 86400.0, 0.0)
        weight = 0.5 ** (age_days / half_life_days)
        weighted.append((vector, weight, f"Close to your saved paper: {title}"))
    if get_settings().foryou_events:
        for _paper_id, title, engaged_at, vector in positive_event_vectors(
            session, user_sub, embedder.model_id
        ):
            age_days = max((now - engaged_at).total_seconds() / 86400.0, 0.0)
            weight = _EVENT_WEIGHT * 0.5 ** (age_days / half_life_days)
            weighted.append((vector, weight, f"Like a paper you read: {title}"))
    for interest in interests:
        cleaned = interest.strip()
        if cleaned:
            weighted.append(
                (embedder.embed_query(cleaned), 1.0, f"Matches your interest: {cleaned}")
            )
    if not weighted:
        return []

    import numpy as np

    vectors = np.array([vector for vector, _, _ in weighted], dtype=float)
    weights = np.array([weight for _, weight, _ in weighted], dtype=float)
    n_clusters = max(1, min(k, len(weighted)))
    if n_clusters == 1:
        centers = ((vectors * weights[:, None]).sum(axis=0) / max(float(weights.sum()), 1e-12))[
            None, :
        ]
        labels = np.zeros(len(weighted), dtype=int)
    else:
        from sklearn.cluster import KMeans

        model = KMeans(n_clusters=n_clusters, n_init=10, random_state=0)
        labels = model.fit_predict(vectors, sample_weight=weights)
        centers = model.cluster_centers_

    clusters: list[InterestCluster] = []
    for index in range(centers.shape[0]):
        members = [i for i in range(len(weighted)) if int(labels[i]) == index]
        if not members:
            continue
        center = centers[index]
        norm = float(np.linalg.norm(center))
        unit = center / norm if norm > 0 else center
        anchor = max(members, key=lambda i: weights[i])
        clusters.append(
            InterestCluster(centroid=[float(value) for value in unit], reason=weighted[anchor][2])
        )
    return clusters


def _mmr(entries: list[_Entry], *, k: int, lam: float) -> list[_Entry]:
    """Greedy maximal marginal relevance over score-normalized entries (anti-redundancy)."""
    if not entries:
        return []
    max_score = entries[0].score or 1.0
    remaining = list(entries)
    selected: list[_Entry] = []
    while remaining and len(selected) < k:

        def gain(entry: _Entry) -> float:
            redundancy = max(
                (_cosine(entry.vector, chosen.vector) for chosen in selected), default=0.0
            )
            return lam * (entry.score / max_score) - (1.0 - lam) * redundancy

        best = max(remaining, key=gain)
        selected.append(best)
        remaining.remove(best)
    return selected


def personalized_papers(
    session: Session,
    embedder: Embedder,
    interests: list[str],
    *,
    user_sub: str | None = None,
    k: int = 20,
    days: int,
    half_life_days: float = _DEFAULT_HALF_LIFE_DAYS,
) -> list[PersonalizedPaper]:
    """Window papers by profile similarity x recency x breakthrough; empty on cold start.

    With ``RS_FORYOU_EVENTS`` on, papers the reader has dismissed (the bounded working set
    plus the event log's full memory) never surface here — a dismissal is the clearest
    negative the log holds, and a personalized feed that re-recommends it reads as deaf.
    """
    settings = get_settings()
    if settings.foryou_centroids >= 1 and user_sub is not None:
        clusters = profile_clusters(
            session,
            embedder,
            user_sub,
            interests,
            k=settings.foryou_centroids,
            half_life_days=settings.foryou_half_life_days,
        )
    else:
        centroid = interest_centroid(embedder, interests)
        clusters = [InterestCluster(centroid=centroid, reason=None)] if centroid else []
    if not clusters:
        return []

    excluded: set[str] = set()
    if settings.foryou_events and user_sub is not None:
        excluded = set(dismissed_event_paper_ids(session, user_sub))
        excluded.update(dismissed_papers(session, user_sub))

    rows = window_vectors(session, days=days, model_id=embedder.model_id)
    if not rows:
        return []
    papers = get_papers(session, [paper_id for paper_id, _, _ in rows])
    boosts = breakthrough_many(session, list(papers))

    entries: list[_Entry] = []
    for paper_id, _title, vector in rows:
        paper = papers.get(paper_id)
        if paper is None or paper_id in excluded:
            continue
        similarities = [_cosine(cluster.centroid, vector) for cluster in clusters]
        best = max(range(len(similarities)), key=similarities.__getitem__)
        similarity = max(similarities[best], 0.0)
        score = (
            similarity
            * _recency_weight(paper.published_at, half_life_days)
            * (1.0 + boosts[paper_id].total)
        )
        entries.append(
            _Entry(
                paper=paper,
                vector=vector,
                similarity=similarity,
                score=score,
                reason=clusters[best].reason if similarity > 0 else None,
            )
        )
    entries.sort(key=lambda entry: entry.score, reverse=True)

    explore_slots = min(settings.foryou_explore_slots, k)
    picks = k - explore_slots
    if settings.foryou_mmr_lambda < 1.0:
        pool = entries[: max(picks * 3, picks)]
        selected = _mmr(pool, k=picks, lam=settings.foryou_mmr_lambda)
    else:
        selected = entries[:picks]

    if explore_slots:
        chosen = {entry.paper.id for entry in selected}
        outsiders = [
            entry
            for entry in entries
            if entry.similarity < _EXPLORE_SIMILARITY_CEILING and entry.paper.id not in chosen
        ]
        outsiders.sort(key=lambda entry: boosts[entry.paper.id].total, reverse=True)
        for entry in outsiders[:explore_slots]:
            entry.reason = _EXPLORE_REASON
            selected.append(entry)

    return [
        PersonalizedPaper(
            paper=entry.paper,
            score=entry.score,
            distance=1.0 - entry.similarity,
            reason=entry.reason,
        )
        for entry in selected[:k]
    ]
