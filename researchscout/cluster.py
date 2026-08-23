"""Cluster the window's papers into emerging topics and rank them by momentum.

Groups papers whose embeddings sit close together (agglomerative clustering on cosine distance),
scores each cluster by the summed breakthrough of its members, labels it with the LLM, and returns
the clusters momentum-first. This is what turns a flat feed into "these themes are heating up right
now" — the clustering the product has always promised but never had.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.llm.base import LLM
from researchscout.llm.errors import is_quota_error
from researchscout.score import breakthrough
from researchscout.store.topics import window_vectors

logger = logging.getLogger(__name__)

_LABEL_SYSTEM = (
    "You label clusters of AI/ML research papers. Given discriminative keywords and "
    "representative titles from one cluster, reply with a 2-5 word topic label on the first "
    "line and a single-sentence summary on the second line. No preamble, no markdown, no "
    "quotes."
)
_MEMBERS_STORED = 8
_KEYWORDS_TOP_K = 8


@dataclass
class Member:
    paper_id: str
    title: str
    score: float


@dataclass
class Topic:
    label: str
    summary: str | None
    score: float
    size: int
    members: list[Member]
    # Unit-mean member embedding: how the store matches a rebuilt topic to its previous self.
    centroid: list[float] = field(default_factory=list)


@dataclass
class TopicBuild:
    """A build's topics plus how their labels were made — the ledger note's raw material."""

    topics: list[Topic]
    llm_labels: int = 0
    fallback_labels: int = 0


def cluster_labels(
    vectors: list[list[float]], *, threshold: float, algo: str = "agglomerative"
) -> list[int]:
    """Cluster id per vector; ``-1`` marks HDBSCAN outliers (agglomerative assigns everything).

    agglomerative merges points within the cosine ``threshold``. hdbscan (sklearn-native since
    1.3, no extra dependency) finds density peaks with no fixed count and leaves misfits in
    the ``-1`` pool — genuinely informative for a radar: a paper with no cohort yet may be an
    emerging topic of one. ``threshold`` is ignored under hdbscan.
    """
    if not vectors:
        return []
    import numpy as np

    data = np.array(vectors, dtype=float)
    if algo == "hdbscan":
        if len(vectors) < 2:
            return [-1 for _ in vectors]
        from sklearn.cluster import HDBSCAN

        model = HDBSCAN(min_cluster_size=2, metric="cosine", copy=True)
        return [int(label) for label in model.fit_predict(data)]
    if len(vectors) == 1:
        return [0]
    from sklearn.cluster import AgglomerativeClustering

    agglomerative = AgglomerativeClustering(
        n_clusters=None, distance_threshold=threshold, metric="cosine", linkage="average"
    )
    return [int(label) for label in agglomerative.fit_predict(data)]


def cluster_keywords(
    docs_by_cluster: dict[int, list[str]], *, top_k: int = _KEYWORDS_TOP_K
) -> dict[int, list[str]]:
    """Class-based TF-IDF keywords per cluster (the BERTopic trick, in plain sklearn).

    Each cluster's documents concatenate into one class document; a term's weight is its
    within-class frequency scaled by how rare it is across classes, so the keywords are what
    DISTINGUISHES a cluster, not what is merely common in it.
    """
    if not docs_by_cluster:
        return {}
    import numpy as np
    from sklearn.feature_extraction.text import CountVectorizer

    keys = sorted(docs_by_cluster)
    class_docs = [" ".join(docs_by_cluster[key]) for key in keys]
    vectorizer = CountVectorizer(stop_words="english", ngram_range=(1, 2))
    try:
        counts = vectorizer.fit_transform(class_docs).toarray().astype(float)
    except ValueError:  # empty vocabulary: everything was a stop word
        return {key: [] for key in keys}
    terms = vectorizer.get_feature_names_out()
    tf = counts / np.maximum(counts.sum(axis=1, keepdims=True), 1.0)
    average_class_words = counts.sum() / max(len(keys), 1)
    term_frequency = np.maximum(counts.sum(axis=0), 1.0)
    weights = tf * np.log(1.0 + average_class_words / term_frequency)
    keywords: dict[int, list[str]] = {}
    for row, key in enumerate(keys):
        order = np.argsort(weights[row])[::-1]
        keywords[key] = [str(terms[i]) for i in order[:top_k] if weights[row][i] > 0]
    return keywords


def unit_centroid(vectors: list[list[float]]) -> list[float]:
    """The cluster's mean embedding scaled to unit length (cosine-comparable across builds)."""
    import numpy as np

    arr = np.array(vectors, dtype=float)
    centroid = arr.mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm > 0:
        centroid = centroid / norm
    return [float(value) for value in centroid]


def representative_order(vectors: list[list[float]]) -> list[int]:
    """Member indices sorted nearest-to-centroid first (cosine): the most typical documents.

    Feeding these to the labeler beats high-momentum members — momentum picks outliers, the
    centroid picks what the cluster is actually about — and caps prompt size by cluster shape,
    not cluster size.
    """
    import numpy as np

    arr = np.array(vectors, dtype=float)
    centroid = arr.mean(axis=0)
    denominator = np.linalg.norm(arr, axis=1) * np.linalg.norm(centroid)
    similarity = arr @ centroid / np.maximum(denominator, 1e-12)
    return [int(i) for i in np.argsort(similarity)[::-1]]


def _keyword_label(keywords: list[str] | None) -> str:
    """Deterministic label from the cluster's own c-TF-IDF terms (top three, comma-joined)."""
    if not keywords:
        return "Untitled topic"
    return ", ".join(keywords[:3])


def label_topic(
    llm: LLM, titles: list[str], keywords: list[str] | None = None
) -> tuple[str, str | None]:
    """Ask the LLM for a short label and one-line summary; parse its two lines defensively."""
    parts = []
    if keywords:
        parts.append("Keywords: " + ", ".join(keywords))
    parts.append("Representative titles:\n" + "\n".join(f"- {title}" for title in titles))
    prompt = "\n\n".join(parts)
    lines = [
        line.strip() for line in llm.complete(_LABEL_SYSTEM, prompt).splitlines() if line.strip()
    ]
    label = lines[0] if lines else "Untitled topic"
    summary = lines[1] if len(lines) > 1 else None
    return label, summary


def build_topics(
    session: Session,
    embedder: Embedder,
    llm: LLM,
    *,
    days: int,
    threshold: float,
    min_size: int = 2,
    max_topics: int = 12,
    label_titles: int = 4,
    algo: str = "agglomerative",
) -> TopicBuild:
    """Cluster the window's embedded papers, score and label each cluster, momentum-first.

    Labels come from c-TF-IDF keywords plus the ``label_titles`` most representative
    (nearest-centroid) member titles, so the prompt stays small and on-center regardless of
    cluster size. Under hdbscan the ``-1`` outlier pool is excluded — those papers have no
    cohort, and one giant pseudo-topic of misfits would be worse than none.

    Clusters are ranked before any labeling, so at most ``max_topics`` prompts go to the
    model per build; a label failure falls back to the cluster's own keywords, and the first
    quota error stops asking for the rest of the build — a spent daily cap answers every
    later call the same way.
    """
    rows = window_vectors(session, days=days, model_id=embedder.model_id)
    if not rows:
        return TopicBuild([])

    labels = cluster_labels([vector for _, _, vector in rows], threshold=threshold, algo=algo)
    groups: dict[int, list[tuple[str, str, list[float]]]] = {}
    for (paper_id, title, vector), cluster in zip(rows, labels, strict=True):
        if cluster == -1:
            continue
        groups.setdefault(cluster, []).append((paper_id, title, vector))

    eligible = {cluster: members for cluster, members in groups.items() if len(members) >= min_size}
    keywords = cluster_keywords(
        {cluster: [title for _, title, _ in members] for cluster, members in eligible.items()}
    )

    candidates: list[tuple[int, list[tuple[str, str, list[float]]], list[Member]]] = []
    for cluster, members in eligible.items():
        scored = sorted(
            (Member(pid, title, breakthrough(session, pid).total) for pid, title, _ in members),
            key=lambda member: member.score,
            reverse=True,
        )
        candidates.append((cluster, members, scored))
    candidates.sort(key=lambda entry: sum(member.score for member in entry[2]), reverse=True)
    del candidates[max_topics:]

    topics: list[Topic] = []
    llm_labels = fallback_labels = 0
    llm_available = True
    for cluster, members, scored in candidates:
        typical = representative_order([vector for _, _, vector in members])
        typical_titles = [members[i][1] for i in typical[:label_titles]]
        words = keywords.get(cluster)
        summary: str | None = None
        if llm_available:
            try:
                label, summary = label_topic(llm, typical_titles, words)
                llm_labels += 1
            except Exception as exc:  # noqa: BLE001 - the keyword label is the safe floor
                logger.warning("topic label failed; keyword fallback", exc_info=True)
                if is_quota_error(exc):
                    llm_available = False
                label = _keyword_label(words)
                fallback_labels += 1
        else:
            label = _keyword_label(words)
            fallback_labels += 1
        topics.append(
            Topic(
                label=label,
                summary=summary,
                score=sum(member.score for member in scored),
                size=len(scored),
                members=scored[:_MEMBERS_STORED],
                centroid=unit_centroid([vector for _, _, vector in members]),
            )
        )

    return TopicBuild(topics, llm_labels, fallback_labels)
