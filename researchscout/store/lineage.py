"""Per-packet lineage rows and the hourly rollups behind the stream stats endpoint.

Every envelope's lineage stamps land here, one row per (event_id, stage), transactional with
the stage's data writes and upserted so at-least-once redelivery converges. Grafana can point
at the table (or the pipeline_rollups_hourly view from migration 0016) directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from researchscout.store.models import PipelineLineageRow
from researchscout.stream.envelope import Envelope

_UPDATED_COLUMNS = (
    "kind",
    "source",
    "paper_id",
    "category",
    "topic",
    "entered_at",
    "exited_at",
    "outcome",
    "error",
)


def _stamp_rows(envelope: Envelope) -> dict[tuple[str, str], dict[str, Any]]:
    """One parameter dict per (event_id, stage), later stamps winning over earlier ones.

    The dedupe matters: a packet retried through the serial fallback carries two stamps
    for the same stage, and ON CONFLICT cannot touch the same row twice in one statement.
    """
    enrichment = envelope.payload.get("enrichment") or {}
    topic = enrichment.get("topic") or {}
    key = envelope.key()
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for stamp in envelope.lineage:
        rows[(envelope.event_id, stamp.stage)] = {
            "event_id": envelope.event_id,
            "stage": stamp.stage,
            "kind": envelope.kind,
            "source": envelope.source,
            "paper_id": key if ":" in key else None,
            "category": enrichment.get("group"),
            "topic": topic.get("label") if isinstance(topic, dict) else None,
            "entered_at": stamp.entered_at,
            "exited_at": stamp.exited_at,
            "outcome": stamp.outcome,
            "error": stamp.error,
        }
    return rows


def record_stages_many(session: Session, envelopes: Sequence[Envelope]) -> int:
    """Upsert every stamp of every envelope in one executemany; returns the row count."""
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for envelope in envelopes:
        rows.update(_stamp_rows(envelope))
    if not rows:
        return 0
    stmt = insert(PipelineLineageRow)
    stmt = stmt.on_conflict_do_update(
        index_elements=["event_id", "stage"],
        set_={column: stmt.excluded[column] for column in _UPDATED_COLUMNS},
    )
    session.execute(stmt, list(rows.values()))
    return len(rows)


def record_stages(session: Session, envelope: Envelope) -> int:
    """Upsert one lineage row per stamp on the envelope; returns the stamp count."""
    return record_stages_many(session, [envelope])


def hourly_stats(session: Session, *, hours: int = 24) -> list[dict[str, Any]]:
    """Hourly per-stage rollups for the stats endpoint, newest bucket first."""
    moment = func.coalesce(PipelineLineageRow.exited_at, PipelineLineageRow.entered_at)
    bucket = func.date_trunc("hour", moment).label("bucket")
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    rows = session.execute(
        select(
            bucket,
            PipelineLineageRow.stage,
            PipelineLineageRow.kind,
            PipelineLineageRow.source,
            PipelineLineageRow.outcome,
            PipelineLineageRow.category,
            func.count().label("packets"),
            func.avg(
                func.extract("epoch", PipelineLineageRow.exited_at - PipelineLineageRow.entered_at)
            ).label("avg_seconds"),
        )
        .where(moment >= cutoff)
        .group_by(
            bucket,
            PipelineLineageRow.stage,
            PipelineLineageRow.kind,
            PipelineLineageRow.source,
            PipelineLineageRow.outcome,
            PipelineLineageRow.category,
        )
        .order_by(bucket.desc(), PipelineLineageRow.stage)
    ).all()
    return [
        {
            "bucket": row.bucket,
            "stage": row.stage,
            "kind": row.kind,
            "source": row.source,
            "outcome": row.outcome,
            "category": row.category,
            "packets": row.packets,
            "avg_seconds": float(row.avg_seconds) if row.avg_seconds is not None else None,
        }
        for row in rows
    ]


def prune_lineage(session: Session, *, older_than_days: int = 30) -> int:
    """Delete lineage older than the horizon; returns the deleted count (rides the daily report)."""
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    result = session.execute(
        delete(PipelineLineageRow)
        .where(PipelineLineageRow.entered_at < cutoff)
        .returning(PipelineLineageRow.event_id)
    )
    return len(result.all())
