"""Per-packet lineage rows and the hourly rollups behind the stream stats endpoint.

Every envelope's lineage stamps land here, one row per (event_id, stage), transactional with
the stage's data writes and upserted so at-least-once redelivery converges. Grafana can point
at the table (or the pipeline_rollups_hourly view from migration 0016) directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from researchscout.store.models import PipelineLineageRow
from researchscout.stream.envelope import Envelope


def record_stages(session: Session, envelope: Envelope) -> int:
    """Upsert one lineage row per stamp on the envelope; returns the stamp count."""
    enrichment = envelope.payload.get("enrichment") or {}
    topic = enrichment.get("topic") or {}
    key = envelope.key()
    for stamp in envelope.lineage:
        stmt = insert(PipelineLineageRow).values(
            event_id=envelope.event_id,
            stage=stamp.stage,
            kind=envelope.kind,
            source=envelope.source,
            paper_id=key if ":" in key else None,
            category=enrichment.get("group"),
            topic=topic.get("label") if isinstance(topic, dict) else None,
            entered_at=stamp.entered_at,
            exited_at=stamp.exited_at,
            outcome=stamp.outcome,
            error=stamp.error,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["event_id", "stage"],
            set_={
                key_: stmt.excluded[key_]
                for key_ in (
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
            },
        )
        session.execute(stmt)
    return len(envelope.lineage)


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
