"""Storage layer: Postgres engine, ORM models, and idempotent persistence helpers."""

from researchscout.store import models  # noqa: F401  (import registers tables on Base.metadata)
from researchscout.store.db import Base, engine, session_scope
from researchscout.store.papers import (
    find_by_external_id,
    get_paper,
    link_external_ids,
    upsert_paper,
)
from researchscout.store.raw import append_raw

__all__ = [
    "Base",
    "append_raw",
    "engine",
    "find_by_external_id",
    "get_paper",
    "link_external_ids",
    "session_scope",
    "upsert_paper",
]
