"""Cluster the window's papers into emerging topics and rank them by momentum.

Groups papers whose embeddings sit close together (agglomerative clustering on cosine distance),
scores each cluster by the summed breakthrough of its members, labels it with the LLM, and returns
the clusters momentum-first. This is what turns a flat feed into "these themes are heating up right
now" — the clustering the product has always promised but never had.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.llm.base import LLM
from researchscout.score import breakthrough
from researchscout.store.topics import window_vectors

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


def cluster_labels(vectors: list[list[float]], *, threshold: float) -> list[int]:
    """Agglomerative cluster id per vector; merges points within ``threshold`` cosine distance."""
    if not vectors:
        return []
    if len(vectors) == 1:
        return [0]
    import numpy as np
    from sklearn.cluster import AgglomerativeClustering

    model = AgglomerativeClustering(
        n_clusters=None, distance_threshold=threshold, metric="cosine", linkage="average"
    )
    return [int(label) for label in model.fit_predict(np.array(vectors, dtype=float))]


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
) -> list[Topic]:
    """Cluster the window's embedded papers, score and label each cluster, momentum-first.

    Labels come from c-TF-IDF keywords plus the ``label_titles`` most representative
    (nearest-centroid) member titles, so the prompt stays small and on-center regardless of
    cluster size.
    """
    rows = window_vectors(session, days=days, model_id=embedder.model_id)
    if not rows:
        return []

    labels = cluster_labels([vector for _, _, vector in rows], threshold=threshold)
    groups: dict[int, list[tuple[str, str, list[float]]]] = {}
    for (paper_id, title, vector), cluster in zip(rows, labels, strict=True):
        groups.setdefault(cluster, []).append((paper_id, title, vector))

    eligible = {cluster: members for cluster, members in groups.items() if len(members) >= min_size}
    keywords = cluster_keywords(
        {cluster: [title for _, title, _ in members] for cluster, members in eligible.items()}
    )

    topics: list[Topic] = []
    for cluster, members in eligible.items():
        typical = representative_order([vector for _, _, vector in members])
        typical_titles = [members[i][1] for i in typical[:label_titles]]
        scored = sorted(
            (Member(pid, title, breakthrough(session, pid).total) for pid, title, _ in members),
            key=lambda member: member.score,
            reverse=True,
        )
        label, summary = label_topic(llm, typical_titles, keywords.get(cluster))
        topics.append(
            Topic(
                label=label,
                summary=summary,
                score=sum(member.score for member in scored),
                size=len(scored),
                members=scored[:_MEMBERS_STORED],
            )
        )

    topics.sort(key=lambda topic: topic.score, reverse=True)
    return topics[:max_topics]
