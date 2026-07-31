"""The corpus keyword dictionary.

A public read like the topics and papers routes, so no auth and no rate limit. It is
one cheap aggregate over existing data serving the chat drawer's pattern matching,
which is also why it carries no RS_* flag.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from researchscout.api.deps import get_session
from researchscout.api.schemas import KeywordCount, KeywordList
from researchscout.store.keywords import keyword_counts

router = APIRouter(tags=["keywords"])


@router.get("/keywords")
def keywords_index(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> KeywordList:
    ranked, total = keyword_counts(session, limit=limit)
    return KeywordList(
        items=[KeywordCount(keyword=keyword, papers=papers) for keyword, papers in ranked],
        total=total,
    )
