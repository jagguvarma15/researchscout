"""The authenticated reading list: save, curate, filter and export what you saved."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_session
from researchscout.api.schemas import SavedList, SavedPaperItem, SavedUpdate
from researchscout.export import bibtex_export, csv_export
from researchscout.store.papers import get_paper
from researchscout.store.saved import (
    list_saved,
    save_paper,
    saved_tags,
    unsave_paper,
    update_saved,
)

router = APIRouter(tags=["saved"])


@router.post("/papers/{paper_id:path}/save")
def save(
    paper_id: str,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, bool]:
    """Add a paper to the caller's reading list (idempotent)."""
    if get_paper(session, paper_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown paper id: {paper_id}")
    save_paper(session, user.sub, paper_id)
    return {"saved": True}


@router.patch("/papers/{paper_id:path}/save")
def update(
    paper_id: str,
    body: SavedUpdate,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, bool]:
    """Change a saved row's status, tags or note.

    Only fields the request actually carried are applied - ``model_fields_set`` is what
    keeps "absent" and "clear this" distinct, which a plain None check cannot.
    """
    changes = {field: getattr(body, field) for field in body.model_fields_set}
    if not update_saved(session, user.sub, paper_id, changes):
        raise HTTPException(status_code=404, detail=f"not in the reading list: {paper_id}")
    return {"saved": True}


@router.delete("/papers/{paper_id:path}/save")
def unsave(
    paper_id: str,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, bool]:
    """Remove a paper from the caller's reading list (idempotent)."""
    unsave_paper(session, user.sub, paper_id)
    return {"saved": False}


@router.get("/me/saved/export")
def export_saved(
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
    format: Annotated[Literal["bibtex", "csv"], Query()] = "bibtex",
) -> PlainTextResponse:
    """The whole library as a downloadable file. Declared before /me/saved's siblings so
    nothing ever reads "export" as data."""
    entries = list_saved(session, user.sub)
    if format == "csv":
        return PlainTextResponse(
            csv_export(entries),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="reading-list.csv"'},
        )
    return PlainTextResponse(
        bibtex_export(entries),
        media_type="application/x-bibtex",
        headers={"Content-Disposition": 'attachment; filename="reading-list.bib"'},
    )


@router.get("/me/saved")
def my_saved(
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
    status: Annotated[Literal["to-read", "reading", "done"] | None, Query()] = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: Annotated[Literal["saved", "published", "title"], Query()] = "saved",
) -> SavedList:
    """The caller's reading list with its library fields, filtered and ordered."""
    entries = list_saved(session, user.sub, status=status, tag=tag, query=q, sort=sort)
    items = []
    for entry in entries:
        item = SavedPaperItem.from_paper(entry.paper)
        item.status = entry.status
        item.tags = entry.tags
        item.note = entry.note
        item.saved_at = entry.saved_at
        items.append(item)
    return SavedList(items=items, tags=saved_tags(session, user.sub))
