"""Append-only raw landing — unmodified source payloads kept for replay."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from researchscout.store.models import RawItemRow


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
