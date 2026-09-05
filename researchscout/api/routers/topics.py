"""Public topic endpoints: the momentum-ranked emerging-topic list and one topic's papers."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from researchscout.api.deps import get_session
from researchscout.api.schemas import TopicDetail, TopicHistoryPoint, TopicList, TopicPaper
from researchscout.store.models import TopicRow
from researchscout.store.topics import PaperMeta, get_topic, list_topics, paper_meta

router = APIRouter(tags=["topics"])


def _detail(row: TopicRow, meta: dict[str, PaperMeta] | None = None) -> TopicDetail:
    """Shape one topic row; ``meta`` (detail page only) adds each member's field and date."""
    meta = meta or {}
    papers = []
    for paper in row.papers:
        info = meta.get(paper["paper_id"])
        papers.append(
            TopicPaper(
                paper_id=paper["paper_id"],
                title=paper["title"],
                score=paper["score"],
                primary_category=info.primary_category if info else None,
                published_at=info.published_at if info else None,
            )
        )
    return TopicDetail(
        id=row.id,
        label=row.label,
        summary=row.summary,
        score=row.score,
        size=row.size,
        trend=row.trend,
        history=[TopicHistoryPoint.model_validate(point) for point in row.history or []],
        papers=papers,
    )


@router.get("/topics")
def topics_index(session: Annotated[Session, Depends(get_session)]) -> TopicList:
    """Emerging topics, most momentum first, each with its top papers."""
    return TopicList(items=[_detail(row) for row in list_topics(session)])


@router.get("/topics/{topic_id}")
def topic_detail(topic_id: int, session: Annotated[Session, Depends(get_session)]) -> TopicDetail:
    """One topic with its member papers, each carrying its field and publish date."""
    row = get_topic(session, topic_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown topic: {topic_id}")
    meta = paper_meta(session, [paper["paper_id"] for paper in row.papers])
    return _detail(row, meta)
