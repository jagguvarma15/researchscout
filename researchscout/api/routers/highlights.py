"""Synced reader highlights, behind RS_HIGHLIGHTS_SYNC.

Off (the default) both routes 404 and the reader's marks stay purely in the browser -
byte-identical to before the feature existed. The client treats a 404 as "sync is off"
and simply carries on locally.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_session
from researchscout.api.schemas import HighlightBody, HighlightRectBody, HighlightsPayload
from researchscout.config import get_settings
from researchscout.store.highlights import HighlightRecord, list_highlights, replace_highlights
from researchscout.store.papers import get_paper

router = APIRouter(tags=["highlights"])


def _require_enabled() -> None:
    if not get_settings().highlights_sync:
        raise HTTPException(status_code=404, detail="highlight sync is not enabled")


@router.get("/me/highlights/{paper_id:path}")
def paper_highlights(
    paper_id: str,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> HighlightsPayload:
    """The caller's synced marks on one paper (empty when none)."""
    _require_enabled()
    return HighlightsPayload(
        items=[
            HighlightBody(
                id=record.id,
                page=record.page,
                color=record.color,
                text=record.text,
                note=record.note,
                rects=[HighlightRectBody(**rect) for rect in record.rects],
            )
            for record in list_highlights(session, user.sub, paper_id)
        ]
    )


@router.put("/me/highlights/{paper_id:path}")
def put_paper_highlights(
    paper_id: str,
    body: HighlightsPayload,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, int]:
    """Replace the caller's marks on one paper with the client's list (empty clears)."""
    _require_enabled()
    if get_paper(session, paper_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown paper id: {paper_id}")
    stored = replace_highlights(
        session,
        user.sub,
        paper_id,
        [
            HighlightRecord(
                id=item.id,
                page=item.page,
                color=item.color,
                text=item.text,
                note=item.note,
                rects=[rect.model_dump() for rect in item.rects],
            )
            for item in body.items
        ],
    )
    return {"stored": stored}
