"""Ingest cursor/state persistence — one row per source."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from researchscout.store.models import IngestStateRow


def save_state(session: Session, source: str, cursor: str | None, last_since: datetime) -> None:
    """Record where ingestion for ``source`` left off (idempotent upsert)."""
    stmt = insert(IngestStateRow).values(source=source, cursor=cursor, last_since=last_since)
    stmt = stmt.on_conflict_do_update(
        index_elements=["source"],
        set_={"cursor": cursor, "last_since": last_since, "updated_at": func.now()},
    )
    session.execute(stmt)


def get_state(session: Session, source: str) -> tuple[str | None, datetime | None]:
    """Return the saved ``(cursor, last_since)`` for ``source``, or ``(None, None)``."""
    row = session.get(IngestStateRow, source)
    if row is None:
        return None, None
    return row.cursor, row.last_since


def read_state(
    session: Session, source: str
) -> tuple[str | None, datetime | None, datetime | None]:
    """The full state row: ``(cursor, last_since, updated_at)``, all ``None`` when absent.

    ``updated_at`` is the per-source watermark: it stops moving the moment the source stops
    completing pages, which is what lets the ingest window widen by itself over downtime.
    """
    row = session.get(IngestStateRow, source)
    if row is None:
        return None, None, None
    return row.cursor, row.last_since, row.updated_at
