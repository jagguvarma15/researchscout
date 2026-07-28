"""Public paper endpoints: recency feed, faceted filtering, semantic search, and detail."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from researchscout.api.deps import get_embedder, get_session
from researchscout.api.schemas import PaperList, PaperSummary
from researchscout.embed.base import Embedder
from researchscout.retrieve.search import retrieve
from researchscout.store.facets import PaperFacets
from researchscout.store.papers import count_papers, get_paper, list_papers

router = APIRouter(tags=["papers"])


@router.get("/papers")
def papers_index(
    session: Annotated[Session, Depends(get_session)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    q: Annotated[str | None, Query(max_length=500)] = None,
    days: Annotated[int | None, Query(ge=1, le=365)] = None,
    year: Annotated[int | None, Query(ge=2007, le=2100)] = None,
    month: Annotated[int | None, Query(ge=1, le=12)] = None,
    category: Annotated[list[str] | None, Query()] = None,
    kind: Annotated[Literal["tech", "non_tech", "ai"] | None, Query()] = None,
    group: Annotated[list[str] | None, Query()] = None,
    author: Annotated[str | None, Query(max_length=100)] = None,
    venue: Annotated[str | None, Query(max_length=100)] = None,
    min_citations: Annotated[int | None, Query(ge=0)] = None,
    sort: Annotated[Literal["newest", "citations", "activity"], Query()] = "newest",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaperList:
    """List papers filtered by facets and ordered by ``sort``, or ranked against ``q``.

    Under ``q`` the facets still apply to both retrieval legs, but ``sort``/``offset`` are
    inert and ``total`` is null — search returns at most ``limit`` ranked results.
    """
    if month is not None and year is None:
        raise HTTPException(status_code=422, detail="month requires year")
    if days is not None and year is not None:
        raise HTTPException(status_code=422, detail="days and year are mutually exclusive")

    facets = PaperFacets(
        days=days,
        year=year,
        month=month,
        categories=category or None,
        kind=kind,
        groups=group or None,
        author=author,
        venue=venue,
        min_citations=min_citations,
    )
    if q:
        # The feed's search box stays on the fast first-stage path; ask/chat rerank instead.
        results = retrieve(session, embedder, q, k=limit, facets=facets, use_rerank=False)
        items = [PaperSummary.from_paper(item.paper, score=item.score) for item in results]
        return PaperList(items=items, total=None, limit=limit, offset=offset)

    papers = list_papers(session, facets=facets, sort=sort, limit=limit, offset=offset)
    items = [PaperSummary.from_paper(paper) for paper in papers]
    return PaperList(items=items, total=count_papers(session, facets), limit=limit, offset=offset)


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
