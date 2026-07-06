"""Public digest endpoints: the weekly archive and one week's digest."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from researchscout.api.deps import get_session
from researchscout.api.schemas import DigestDetail, DigestItem, DigestList, DigestSummary
from researchscout.store.digests import get_digest, list_digests

router = APIRouter(tags=["digests"])


@router.get("/digests")
def digests_index(session: Annotated[Session, Depends(get_session)]) -> DigestList:
    """All digests, newest week first."""
    return DigestList(
        items=[
            DigestSummary(
                slug=row.slug,
                title=row.title,
                period_start=row.period_start,
                period_end=row.period_end,
            )
            for row in list_digests(session)
        ]
    )


@router.get("/digests/{slug}")
def digest_detail(slug: str, session: Annotated[Session, Depends(get_session)]) -> DigestDetail:
    """One week's digest with its ranked papers."""
    row = get_digest(session, slug)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown digest: {slug}")
    return DigestDetail(
        slug=row.slug,
        title=row.title,
        period_start=row.period_start,
        period_end=row.period_end,
        body=row.body,
        items=[DigestItem.model_validate(item) for item in row.items],
    )
