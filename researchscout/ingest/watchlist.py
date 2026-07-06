"""The watchlist: tracked sources/categories managed as Airtable rows, read as ingest config.

Airtable is the source of truth here (it is the editing UI); nothing is mirrored locally.
Expected fields per row: Source (default arxiv), Category (optional), Max (optional int),
Enabled (checkbox).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from researchscout.events.schemas import IngestJob


def jobs_from_rows(rows: Sequence[Mapping[str, Any]], *, since: datetime) -> list[IngestJob]:
    """Turn watchlist rows into ingest jobs; disabled rows are skipped."""
    jobs: list[IngestJob] = []
    for row in rows:
        fields = row.get("fields", {})
        if not fields.get("Enabled"):
            continue
        category = fields.get("Category")
        jobs.append(
            IngestJob(
                source=fields.get("Source") or "arxiv",
                since=since,
                max_items=fields.get("Max"),
                categories=[category] if category else None,
            )
        )
    return jobs
