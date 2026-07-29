"""Persistence for emerging topics — a snapshot rebuilt wholesale, with identity carried over.

Each build replaces every row, but a rebuilt topic inherits the ``topic_key``, size history,
and first-seen time of the most similar previous topic (centroid cosine match), so the trend a
topic shows — new, rising, steady, fading — survives the replacement.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from researchscout.store.models import PaperEmbeddingRow, PaperRow, TopicRow

if TYPE_CHECKING:
    from collections.abc import Sequence

    from researchscout.cluster import Topic

# A rebuilt topic must be at least this centroid-similar to inherit a previous identity.
_MATCH_THRESHOLD = 0.8
_HISTORY_CAP = 30


def window_vectors(
    session: Session, *, days: int, model_id: str
) -> list[tuple[str, str, list[float]]]:
    """(paper_id, title, embedding) for in-window papers that have an embedding for the model."""
    window_start = datetime.now(UTC) - timedelta(days=days)
    rows = session.execute(
        select(PaperRow.id, PaperRow.title, PaperEmbeddingRow.embedding)
        .join(PaperEmbeddingRow, PaperEmbeddingRow.paper_id == PaperRow.id)
        .where(PaperEmbeddingRow.model_id == model_id, PaperRow.published_at >= window_start)
    ).all()
    return [(paper_id, title, list(embedding)) for paper_id, title, embedding in rows]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))


def classify_trend(history: list[dict[str, Any]]) -> str:
    """new | rising | steady | fading, from the last two size observations."""
    sizes = [int(point["size"]) for point in history]
    if len(sizes) < 2:
        return "new"
    if sizes[-1] > sizes[-2]:
        return "rising"
    if sizes[-1] < sizes[-2]:
        return "fading"
    return "steady"


def replace_topics(
    session: Session, topics: Sequence[Topic], *, built_at: datetime | None = None
) -> None:
    """Replace the topic set, carrying identity from the most similar previous topics.

    Matching is greedy in the given (momentum-first) order and one-to-one: each previous
    ``topic_key`` is inherited at most once, and only above the cosine threshold. Unmatched
    topics start a fresh key and history.
    """
    now = built_at or datetime.now(UTC)
    previous = [
        (row.topic_key, list(row.centroid or []), list(row.history or []), row.first_seen)
        for row in session.execute(select(TopicRow)).scalars()
        if row.topic_key and row.centroid
    ]
    session.execute(delete(TopicRow))
    session.flush()

    used: set[str] = set()
    for topic in topics:
        best: tuple[str, list[dict[str, Any]], datetime] | None = None
        best_similarity = 0.0
        for key, centroid, history, first_seen in previous:
            if key in used:
                continue
            similarity = _cosine(topic.centroid, centroid)
            if similarity > best_similarity:
                best = (key, history, first_seen)
                best_similarity = similarity
        if best is not None and best_similarity >= _MATCH_THRESHOLD:
            key, history, first_seen = best
            used.add(key)
        else:
            key, history, first_seen = uuid4().hex, [], now
        history = (history + [{"built_at": now.isoformat(), "size": topic.size}])[-_HISTORY_CAP:]
        session.add(
            TopicRow(
                topic_key=key,
                label=topic.label,
                summary=topic.summary,
                score=topic.score,
                size=topic.size,
                papers=[
                    {"paper_id": m.paper_id, "title": m.title, "score": m.score}
                    for m in topic.members
                ],
                trend=classify_trend(history),
                history=history,
                centroid=topic.centroid or None,
                first_seen=first_seen,
            )
        )
    session.flush()


def list_topics(session: Session, *, limit: int = 50) -> list[TopicRow]:
    """Topics, most momentum first."""
    rows = session.execute(select(TopicRow).order_by(TopicRow.score.desc()).limit(limit)).scalars()
    return list(rows)


def get_topic(session: Session, topic_id: int) -> TopicRow | None:
    return session.get(TopicRow, topic_id)
