"""Raw landing — unmodified source payloads kept for replay, pruned past a retention window."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from researchscout.store.models import RawItemRow

_PRUNE_BATCH = 50_000


def append_raw(
    session: Session,
    *,
    source: str,
    fetched_at: datetime,
    payload: dict[str, Any],
    external_id: str | None = None,
) -> int:
    """Append a raw payload and return its row id."""
    row = RawItemRow(source=source, fetched_at=fetched_at, payload=payload, external_id=external_id)
    session.add(row)
    session.flush()
    return row.id


def prune_raw_items(session: Session, *, keep_days: int) -> int:
    """Delete raw payloads older than the retention window; return how many went.

    Deletes in id batches with a commit each, so the first prune over months of backlog
    never holds one giant transaction. JSONB payloads make this table the store's heaviest;
    the fetched_at index turns each batch into a range scan.
    """
    cutoff = datetime.now(UTC) - timedelta(days=keep_days)
    total = 0
    while True:
        ids = select(RawItemRow.id).where(RawItemRow.fetched_at < cutoff).limit(_PRUNE_BATCH)
        deleted = session.execute(delete(RawItemRow).where(RawItemRow.id.in_(ids))).rowcount
        session.commit()
        total += deleted
        if deleted < _PRUNE_BATCH:
            return total
