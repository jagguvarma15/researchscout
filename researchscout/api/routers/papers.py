"""Public paper endpoints: recency feed, semantic search, and detail."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from researchscout.api.deps import get_embedder, get_session
from researchscout.api.schemas import PaperList, PaperSummary
from researchscout.embed.base import Embedder
from researchscout.retrieve.search import retrieve
from researchscout.store.papers import get_paper, list_papers

router = APIRouter(tags=["papers"])


@router.get("/papers")
def papers_index(
    session: Annotated[Session, Depends(get_session)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    q: Annotated[str | None, Query(max_length=500)] = None,
    days: Annotated[int | None, Query(ge=1, le=365)] = None,
    category: Annotated[str | None, Query(max_length=50)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaperList:
    """List recent papers newest-first, or rank them against ``q`` when given."""
    categories = [category] if category else None
    if q:
        results = retrieve(session, embedder, q, k=limit, days=days, categories=categories)
        items = [PaperSummary.from_paper(item.paper, score=item.score) for item in results]
    else:
        papers = list_papers(session, days=days, categories=categories, limit=limit, offset=offset)
        items = [PaperSummary.from_paper(paper) for paper in papers]
    return PaperList(items=items)


@router.get("/papers/{paper_id:path}")
def paper_detail(
    paper_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> PaperSummary:
    """One paper by canonical id (``:path`` because ids like DOIs contain slashes)."""
    paper = get_paper(session, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail=f"unknown paper id: {paper_id}")
    return PaperSummary.from_paper(paper)
