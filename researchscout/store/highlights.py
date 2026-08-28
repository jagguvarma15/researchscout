"""Synced reader highlights: the flagged server copy of the browser's local marks.

localStorage remains the original (it works signed out and offline); this store exists so
marks survive a device change and the reader's 180-day local sweep. The write is a bulk
replace per paper - the client owns the merged truth of one paper's marks, so partial
upserts would only manufacture conflicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from researchscout.store.models import UserHighlightRow


@dataclass(frozen=True)
class HighlightRecord:
    id: str
    page: int
    color: str
    text: str
    note: str | None
    rects: list[dict[str, float]]


def replace_highlights(
    session: Session, user_sub: str, paper_id: str, items: list[HighlightRecord]
) -> int:
    """Replace one paper's synced marks with the client's list; returns how many stored."""
    session.execute(
        delete(UserHighlightRow).where(
            UserHighlightRow.user_sub == user_sub, UserHighlightRow.paper_id == paper_id
        )
    )
    rows = []
    for item in items:
        values: dict[str, Any] = {
            "user_sub": user_sub,
            "paper_id": paper_id,
            "highlight_id": item.id,
            "page": item.page,
            "color": item.color,
            "text": item.text,
            "rects": item.rects,
        }
        # Omitted rather than bound None so the nullable column stays SQL NULL.
        if item.note is not None:
            values["note"] = item.note
        rows.append(UserHighlightRow(**values))
    session.add_all(rows)
    session.flush()
    return len(rows)


def list_highlights(session: Session, user_sub: str, paper_id: str) -> list[HighlightRecord]:
    """One paper's synced marks, in the order they were made (ids sort by creation)."""
    rows = session.execute(
        select(UserHighlightRow)
        .where(UserHighlightRow.user_sub == user_sub, UserHighlightRow.paper_id == paper_id)
        .order_by(UserHighlightRow.highlight_id)
    ).scalars()
    return [
        HighlightRecord(
            id=row.highlight_id,
            page=row.page,
            color=row.color,
            text=row.text,
            note=row.note,
            rects=list(row.rects),
        )
        for row in rows
    ]
