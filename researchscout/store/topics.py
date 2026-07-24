"""Persistence for emerging topics — a current snapshot the build replaces wholesale."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from researchscout.store.models import PaperEmbeddingRow, PaperRow, TopicRow

if TYPE_CHECKING:
    from collections.abc import Sequence

    from researchscout.cluster import Topic


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


def replace_topics(session: Session, topics: Sequence[Topic]) -> None:
    """Replace the whole topic set with a fresh build (a current snapshot, not an archive)."""
    session.execute(delete(TopicRow))
    session.flush()
    for topic in topics:
        session.add(
            TopicRow(
                label=topic.label,
                summary=topic.summary,
                score=topic.score,
                size=topic.size,
                papers=[
                    {"paper_id": m.paper_id, "title": m.title, "score": m.score}
                    for m in topic.members
                ],
            )
        )
    session.flush()


def list_topics(session: Session, *, limit: int = 50) -> list[TopicRow]:
    """Topics, most momentum first."""
    rows = session.execute(select(TopicRow).order_by(TopicRow.score.desc()).limit(limit)).scalars()
    return list(rows)


def get_topic(session: Session, topic_id: int) -> TopicRow | None:
    return session.get(TopicRow, topic_id)
