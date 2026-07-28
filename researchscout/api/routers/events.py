"""Implicit feedback intake: batched interaction events from the web app."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_session
from researchscout.api.schemas import EventAck, EventBatch
from researchscout.store.events import EventInput, append_events

router = APIRouter(tags=["events"])


@router.post("/events", status_code=202)
def ingest_events(
    body: EventBatch,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> EventAck:
    """Store a batch of interaction events; unknown paper ids are dropped, never errors."""
    stored = append_events(
        session,
        user.sub,
        [
            EventInput(
                event=item.event,
                paper_id=item.paper_id,
                rank=item.rank,
                value=item.value,
                surface=item.surface,
            )
            for item in body.events
        ],
    )
    return EventAck(stored=stored)
