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
    "You label clusters of AI/ML research papers. Given the titles in one cluster, reply "
    "with a 2-5 word topic label on the first line and a single-sentence summary on the "
    "second line. No preamble, no markdown, no quotes."
)
_MEMBERS_STORED = 8


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


def label_topic(llm: LLM, titles: list[str]) -> tuple[str, str | None]:
    """Ask the LLM for a short label and one-line summary; parse its two lines defensively."""
    prompt = "Titles:\n" + "\n".join(f"- {title}" for title in titles)
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
    label_titles: int = 5,
) -> list[Topic]:
    """Cluster the window's embedded papers, score and label each cluster, momentum-first."""
    rows = window_vectors(session, days=days, model_id=embedder.model_id)
    if not rows:
        return []

    labels = cluster_labels([vector for _, _, vector in rows], threshold=threshold)
    groups: dict[int, list[tuple[str, str]]] = {}
    for (paper_id, title, _vector), cluster in zip(rows, labels, strict=True):
        groups.setdefault(cluster, []).append((paper_id, title))

    topics: list[Topic] = []
    for members in groups.values():
        if len(members) < min_size:
            continue
        scored = sorted(
            (Member(pid, title, breakthrough(session, pid).total) for pid, title in members),
            key=lambda member: member.score,
            reverse=True,
        )
        label, summary = label_topic(llm, [member.title for member in scored[:label_titles]])
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
