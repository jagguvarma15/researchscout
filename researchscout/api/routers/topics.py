"""Public topic endpoints: the momentum-ranked emerging-topic list and one topic's papers."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from researchscout.api.deps import get_session
from researchscout.api.schemas import TopicDetail, TopicList, TopicPaper
from researchscout.store.models import TopicRow
from researchscout.store.topics import get_topic, list_topics

router = APIRouter(tags=["topics"])


def _detail(row: TopicRow) -> TopicDetail:
    return TopicDetail(
        id=row.id,
        label=row.label,
        summary=row.summary,
        score=row.score,
        size=row.size,
        papers=[TopicPaper.model_validate(paper) for paper in row.papers],
    )


@router.get("/topics")
def topics_index(session: Annotated[Session, Depends(get_session)]) -> TopicList:
    """Emerging topics, most momentum first, each with its top papers."""
    return TopicList(items=[_detail(row) for row in list_topics(session)])


@router.get("/topics/{topic_id}")
def topic_detail(topic_id: int, session: Annotated[Session, Depends(get_session)]) -> TopicDetail:
    """One topic with its member papers."""
    row = get_topic(session, topic_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown topic: {topic_id}")
    return _detail(row)
