"""Public digest endpoints: the paged archive and one issue."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from researchscout.api.deps import get_session
from researchscout.api.schemas import DigestDetail, DigestItem, DigestList, DigestSummary
from researchscout.store.digests import count_digests, get_digest, list_digests

router = APIRouter(tags=["digests"])


@router.get("/digests")
def digests_index(
    session: Annotated[Session, Depends(get_session)],
    kind: Annotated[Literal["weekly", "daily"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DigestList:
    """Digests newest first, optionally one kind, paged; total counts the same filter."""
    return DigestList(
        items=[
            DigestSummary(
                slug=row.slug,
                kind=row.kind,
                title=row.title,
                period_start=row.period_start,
                period_end=row.period_end,
                item_count=len(row.items or []),
                llm_ok=row.llm_ok,
            )
            for row in list_digests(session, kind=kind, limit=limit, offset=offset)
        ],
        total=count_digests(session, kind=kind),
        limit=limit,
        offset=offset,
    )


@router.get("/digests/{slug}")
def digest_detail(slug: str, session: Annotated[Session, Depends(get_session)]) -> DigestDetail:
    """One issue with its ranked papers."""
    row = get_digest(session, slug)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown digest: {slug}")
    return DigestDetail(
        slug=row.slug,
        kind=row.kind,
        title=row.title,
        period_start=row.period_start,
        period_end=row.period_end,
        item_count=len(row.items or []),
        llm_ok=row.llm_ok,
        body=row.body,
        items=[DigestItem.model_validate(item) for item in row.items],
    )
