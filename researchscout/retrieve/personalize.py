"""Personalized "For You" ranking: reweight recent papers toward a reader's profile.

The default path is v1: stored interest keywords average into one centroid. ``RS_FORYOU_CENTROIDS``
switches on v2: saved papers (weighted down by save age) and interest keywords cluster into up to
K centroids, a paper scores by its best-matching centroid, and that centroid's anchor names the
reason shown in the feed ("Close to your saved paper: ..."). Two optional passes follow: MMR
trades relevance for diversity (``RS_FORYOU_MMR_LAMBDA`` below 1), and exploration slots
(``RS_FORYOU_EXPLORE_SLOTS``) surface high-momentum papers outside every centroid so the feed
never closes into a bubble. With no profile there is nothing to personalize, so the caller falls
back to the global feed.

Candidates come from the pgvector HNSW index, not a scan of the whole window: each centroid is a
query vector, ``store.vectors.search`` returns its nearest freshness-filtered papers, and only
that pool (a few hundred at most) is hydrated and scored. The profile itself - the expensive
KMeans fit - is cached per reader and rebuilt on their writes (see ``profile_cache``); interest
keyword embeddings are cached by text. Note two decay laws live here by design: the profile
weights a save by a true 75-day half-life (``0.5 ** (age/hl)``), while the recency multiplier is
a 14-day e-folding (``_recency_weight``) - the same one retrieval uses, kept identical on purpose.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from researchscout.config import get_settings
from researchscout.embed.base import Embedder
from researchscout.retrieve import profile_cache
from researchscout.retrieve.search import _DEFAULT_HALF_LIFE_DAYS, _recency_weight
from researchscout.schema import Paper
from researchscout.score import breakthrough_many
from researchscout.store.account import dismissed_papers
from researchscout.store.events import dismissed_event_paper_ids, positive_event_vectors
from researchscout.store.facets import PaperFacets, facets_where
from researchscout.store.papers import get_papers, list_papers
from researchscout.store.saved import saved_vectors
from researchscout.store.vectors import embeddings_for
from researchscout.store.vectors import search as vector_search

# A paper this dissimilar to every centroid counts as "outside" the profile for exploration.
_EXPLORE_SIMILARITY_CEILING = 0.5
_EXPLORE_REASON = "Rising outside your usual topics"

# What a click/dwell/open is worth relative to a save. Opening a paper is curiosity, saving
# it is a commitment; the profile should lean on the stronger statement.
_EVENT_WEIGHT = 0.3

# Per centroid, how many nearest papers the ANN pulls as candidates, and how many
# recent-activity papers seed the exploration pool. Both bound the request's work.
_CANDIDATES_PER_CENTROID = 60
_EXPLORE_POOL = 40


@dataclass(frozen=True)
class InterestCluster:
    centroid: list[float]
    reason: str | None


@dataclass(frozen=True)
class FeedProfile:
    """The shape of a reader's profile, for the transparency header (zero extra queries)."""

    interests: int
    saves: int
    reads: int
    centroids: int


@dataclass(frozen=True)
class ProfileBundle:
    clusters: list[InterestCluster]
    profile: FeedProfile


@dataclass
class PersonalizedPaper:
    paper: Paper
    score: float
    distance: float
    reason: str | None


@dataclass
class _Entry:
    paper: Paper
    similarity: float
    score: float
    reason: str | None
    vector: list[float] = field(default_factory=list)


# Interest phrases embed to the same vector every request; cache them by (model_id, text).
_INTEREST_CACHE_CAP = 512
_interest_lock = threading.Lock()
_interest_vectors: OrderedDict[tuple[str, str], list[float]] = OrderedDict()


def _interest_vectors_for(embedder: Embedder, texts: list[str]) -> list[list[float]]:
    """Embed interest phrases, serving cached vectors and batching the misses into one call."""
    if not texts:
        return []
    result: dict[str, list[float]] = {}
    missing: list[str] = []
    with _interest_lock:
        for text in texts:
            key = (embedder.model_id, text)
            hit = _interest_vectors.get(key)
            if hit is None:
                missing.append(text)
            else:
                _interest_vectors.move_to_end(key)
                result[text] = hit
    if missing:
        # Dedupe so a repeated phrase costs one forward pass, not several.
        unique = list(dict.fromkeys(missing))
        vectors = embedder.embed_queries(unique)
        with _interest_lock:
            for text, vector in zip(unique, vectors, strict=True):
                _interest_vectors[(embedder.model_id, text)] = vector
                _interest_vectors.move_to_end((embedder.model_id, text))
                result[text] = vector
            while len(_interest_vectors) > _INTEREST_CACHE_CAP:
                _interest_vectors.popitem(last=False)
    return [result[text] for text in texts]


def clear_interest_cache() -> None:
    """Empty the interest-vector cache (tests, where stubs reuse a model id with new vectors)."""
    with _interest_lock:
        _interest_vectors.clear()


def interest_centroid(embedder: Embedder, interests: list[str]) -> list[float] | None:
    """Average the interest embeddings into one unit vector; None when there are no interests."""
    cleaned = [interest.strip() for interest in interests if interest.strip()]
    if not cleaned:
        return None
    vectors = _interest_vectors_for(embedder, cleaned)
    dim = len(vectors[0])
    mean = [sum(vector[i] for vector in vectors) / len(vectors) for i in range(dim)]
    norm = math.sqrt(sum(value * value for value in mean))
    return [value / norm for value in mean] if norm > 0 else None


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def build_profile(
    session: Session,
    embedder: Embedder,
    user_sub: str,
    interests: list[str],
    *,
    k: int,
    half_life_days: float,
) -> ProfileBundle:
    """Cluster the reader's saved papers, positive events, and interests into up to ``k`` centroids.

    Every centroid carries the reason of its heaviest member - the saved paper, opened paper or
    interest keyword that anchors it - which is what the feed shows as "why this paper". With
    ``RS_FORYOU_EVENTS`` on, papers the reader recently clicked, dwelled on or opened join the
    profile at a fraction of a save's weight, decayed on the same half-life. The counts returned
    alongside describe the profile for the page header and cost no extra queries.
    """
    now = datetime.now(UTC)
    weighted: list[tuple[list[float], float, str]] = []
    saves = 0
    for _paper_id, title, saved_at, vector in saved_vectors(session, user_sub, embedder.model_id):
        age_days = max((now - saved_at).total_seconds() / 86400.0, 0.0)
        weight = 0.5 ** (age_days / half_life_days)
        weighted.append((vector, weight, f"Close to your saved paper: {title}"))
        saves += 1
    reads = 0
    if get_settings().foryou_events:
        for _paper_id, title, engaged_at, vector in positive_event_vectors(
            session, user_sub, embedder.model_id
        ):
            age_days = max((now - engaged_at).total_seconds() / 86400.0, 0.0)
            weight = _EVENT_WEIGHT * 0.5 ** (age_days / half_life_days)
            weighted.append((vector, weight, f"Like a paper you read: {title}"))
            reads += 1
    cleaned_interests = [interest.strip() for interest in interests if interest.strip()]
    for interest, vector in zip(
        cleaned_interests, _interest_vectors_for(embedder, cleaned_interests), strict=True
    ):
        weighted.append((vector, 1.0, f"Matches your interest: {interest}"))

    profile = FeedProfile(interests=len(cleaned_interests), saves=saves, reads=reads, centroids=0)
    if not weighted:
        return ProfileBundle(clusters=[], profile=profile)

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
    return ProfileBundle(
        clusters=clusters,
        profile=FeedProfile(
            interests=profile.interests, saves=saves, reads=reads, centroids=len(clusters)
        ),
    )


def profile_clusters(
    session: Session,
    embedder: Embedder,
    user_sub: str,
    interests: list[str],
    *,
    k: int,
    half_life_days: float,
) -> list[InterestCluster]:
    """The clusters half of :func:`build_profile` (for callers that want only the centroids)."""
    return build_profile(
        session, embedder, user_sub, interests, k=k, half_life_days=half_life_days
    ).clusters


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
    timings: dict[str, float] | None = None,
    profile: dict[str, int] | None = None,
) -> list[PersonalizedPaper]:
    """Rank recent papers by profile similarity x recency x breakthrough; empty on cold start.

    Candidates come from the HNSW index (per centroid), not a full-window scan. Dismissed papers
    (the bounded account working set, plus the event log's full memory when ``RS_FORYOU_EVENTS``
    is on) never surface. ``timings``/``profile`` are optional out-dicts the caller reads for
    observability and the page header; both are cheap no-ops when None.
    """
    from time import perf_counter

    settings = get_settings()
    bundle: ProfileBundle

    start = perf_counter()
    if settings.foryou_centroids >= 1 and user_sub is not None:
        cached = profile_cache.get(user_sub, embedder.model_id, settings.foryou_centroids)
        if cached is not None:
            bundle = cached
            if timings is not None:
                timings["cache_hit"] = 1.0
        else:
            bundle = build_profile(
                session,
                embedder,
                user_sub,
                interests,
                k=settings.foryou_centroids,
                half_life_days=settings.foryou_half_life_days,
            )
            profile_cache.put(user_sub, embedder.model_id, settings.foryou_centroids, bundle)
            if timings is not None:
                timings["cache_hit"] = 0.0
    else:
        centroid = interest_centroid(embedder, interests)
        clusters = [InterestCluster(centroid=centroid, reason=None)] if centroid else []
        cleaned = [interest.strip() for interest in interests if interest.strip()]
        bundle = ProfileBundle(
            clusters=clusters,
            profile=FeedProfile(interests=len(cleaned), saves=0, reads=0, centroids=len(clusters)),
        )
        if timings is not None:
            timings["cache_hit"] = 0.0
    if timings is not None:
        timings["profile_ms"] = round((perf_counter() - start) * 1000, 1)
    if profile is not None:
        profile.update(
            interests=bundle.profile.interests,
            saves=bundle.profile.saves,
            reads=bundle.profile.reads,
            centroids=bundle.profile.centroids,
        )

    clusters = bundle.clusters
    if not clusters:
        return []

    excluded: set[str] = set()
    if user_sub is not None:
        excluded.update(dismissed_papers(session, user_sub))
        if settings.foryou_events:
            excluded.update(dismissed_event_paper_ids(session, user_sub))

    # Candidates: each centroid's nearest freshness-filtered papers, merged by best similarity.
    start = perf_counter()
    where = facets_where(PaperFacets(days=days, exclude=sorted(excluded) or None))
    best: dict[str, tuple[float, int]] = {}
    for index, cluster in enumerate(clusters):
        for paper_id, distance in vector_search(
            session,
            cluster.centroid,
            model_id=embedder.model_id,
            k=_CANDIDATES_PER_CENTROID,
            where=where,
        ):
            similarity = 1.0 - distance
            current = best.get(paper_id)
            if current is None or similarity > current[0]:
                best[paper_id] = (similarity, index)
    if timings is not None:
        timings["search_ms"] = round((perf_counter() - start) * 1000, 1)
        timings["candidates"] = float(len(best))

    explore_slots = min(settings.foryou_explore_slots, k)
    picks = k - explore_slots

    # Signals + hydration over the candidate pool (plus the exploration seed), never the window.
    start = perf_counter()
    explore_papers: list[Paper] = []
    if explore_slots:
        explore_papers = list_papers(
            session,
            facets=PaperFacets(days=days, exclude=sorted(excluded) or None),
            sort="activity",
            limit=_EXPLORE_POOL,
        )
    candidate_papers = get_papers(session, list(best))
    all_ids = list(best) + [paper.id for paper in explore_papers if paper.id not in best]
    boosts = breakthrough_many(session, all_ids)
    if timings is not None:
        timings["signals_ms"] = round((perf_counter() - start) * 1000, 1)

    # Rank: score the candidates, then run MMR and the exploration pass.
    start = perf_counter()
    entries: list[_Entry] = []
    for paper_id, (similarity, cluster_index) in best.items():
        paper = candidate_papers.get(paper_id)
        if paper is None:
            continue
        similarity = max(similarity, 0.0)
        score = (
            similarity
            * _recency_weight(paper.published_at, half_life_days)
            * (1.0 + boosts[paper_id].total)
        )
        entries.append(
            _Entry(
                paper=paper,
                similarity=similarity,
                score=score,
                reason=clusters[cluster_index].reason if similarity > 0 else None,
            )
        )
    entries.sort(key=lambda entry: entry.score, reverse=True)

    if settings.foryou_mmr_lambda < 1.0:
        pool = entries[: max(picks * 3, picks)]
        pool_vectors = embeddings_for(session, [e.paper.id for e in pool], embedder.model_id)
        for entry in pool:
            entry.vector = pool_vectors.get(entry.paper.id, [])
        selected = _mmr([e for e in pool if e.vector], k=picks, lam=settings.foryou_mmr_lambda)
    else:
        selected = entries[:picks]

    if explore_slots and explore_papers:
        chosen = {entry.paper.id for entry in selected}
        centroids = [cluster.centroid for cluster in clusters]
        explore_vectors = embeddings_for(
            session, [paper.id for paper in explore_papers], embedder.model_id
        )
        outsiders: list[_Entry] = []
        for paper in explore_papers:
            # Skip only what is already selected - a low-similarity candidate that missed the
            # ranked cut is exactly what an exploration slot is for.
            if paper.id in chosen:
                continue
            vector = explore_vectors.get(paper.id)
            if vector is None:
                continue
            similarity = max((_cosine(centroid, vector) for centroid in centroids), default=0.0)
            if similarity >= _EXPLORE_SIMILARITY_CEILING:
                continue
            score = (
                max(similarity, 0.0)
                * _recency_weight(paper.published_at, half_life_days)
                * (1.0 + boosts[paper.id].total)
            )
            outsiders.append(
                _Entry(paper=paper, similarity=similarity, score=score, reason=_EXPLORE_REASON)
            )
        outsiders.sort(key=lambda entry: boosts[entry.paper.id].total, reverse=True)
        selected.extend(outsiders[:explore_slots])
    if timings is not None:
        timings["rank_ms"] = round((perf_counter() - start) * 1000, 1)

    return [
        PersonalizedPaper(
            paper=entry.paper,
            score=entry.score,
            distance=1.0 - entry.similarity,
            reason=entry.reason,
        )
        for entry in selected[:k]
    ]
