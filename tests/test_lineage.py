from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from researchscout.api.deps import get_session
from researchscout.api.main import create_app
from researchscout.store.lineage import (
    hourly_stats,
    prune_lineage,
    record_stages,
    record_stages_many,
)
from researchscout.store.models import PipelineLineageRow
from researchscout.stream.envelope import Envelope

pytestmark = pytest.mark.integration


def _processed_envelope(event_id: str = "e1") -> Envelope:
    envelope = Envelope(
        event_id=event_id,
        kind="paper",
        source="arxiv",
        fetched_at=datetime.now(UTC),
        payload={
            "paper": {"id": "arxiv:2607.1"},
            "enrichment": {
                "group": "cs",
                "topic": {"key": "t-1", "label": "Efficient attention", "similarity": 0.8},
            },
        },
    )
    for stage in ("produce", "parse"):
        stamp = envelope.begin(stage)  # type: ignore[arg-type]
        envelope.finish(stamp)
    categorized = envelope.begin("categorize")
    envelope.finish(categorized, detail={"keyword_method": "statistical", "candidate_count": 3})
    failed = envelope.begin("inject")
    envelope.finish(failed, "error", "db unavailable")
    return envelope


def _count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(PipelineLineageRow)).scalar_one()


def test_record_stages_converges_on_redelivery(session: Session) -> None:
    envelope = _processed_envelope()
    assert record_stages(session, envelope) == 4
    assert record_stages(session, envelope) == 4  # replay: same rows, upserted
    session.flush()

    assert _count(session) == 4
    row = session.get(PipelineLineageRow, ("e1", "inject"))
    assert row is not None
    assert row.outcome == "error" and row.error == "db unavailable"
    assert row.paper_id == "arxiv:2607.1"
    assert row.category == "cs" and row.topic == "Efficient attention"
    categorize_row = session.get(PipelineLineageRow, ("e1", "categorize"))
    assert categorize_row is not None
    assert categorize_row.detail == {"keyword_method": "statistical", "candidate_count": 3}


def test_record_stages_many_batches_and_dedupes(session: Session) -> None:
    first = _processed_envelope("m1")
    second = _processed_envelope("m2")
    retried = second.begin("inject")  # a serial retry stamps the same stage again
    second.finish(retried)

    assert record_stages_many(session, [first, second]) == 8  # deduped, one row per stage
    session.flush()

    assert _count(session) == 8
    row = session.get(PipelineLineageRow, ("m2", "inject"))
    assert row is not None and row.outcome == "ok"  # the later stamp won
    unretried = session.get(PipelineLineageRow, ("m1", "inject"))
    assert unretried is not None and unretried.outcome == "error"


def test_hourly_stats_groups_by_bucket_and_stage(session: Session) -> None:
    record_stages(session, _processed_envelope("e1"))
    record_stages(session, _processed_envelope("e2"))
    session.flush()

    stats = hourly_stats(session, hours=24)
    by_stage = {(row["stage"], row["outcome"]): row for row in stats}
    assert by_stage[("parse", "ok")]["packets"] == 2
    assert by_stage[("inject", "error")]["packets"] == 2
    assert by_stage[("parse", "ok")]["avg_seconds"] is not None


def test_prune_lineage_deletes_only_old_rows(session: Session) -> None:
    envelope = _processed_envelope()
    record_stages(session, envelope)
    old = PipelineLineageRow(
        event_id="ancient",
        stage="produce",
        kind="paper",
        source="arxiv",
        entered_at=datetime.now(UTC) - timedelta(days=45),
        outcome="ok",
    )
    session.add(old)
    session.flush()

    assert prune_lineage(session, older_than_days=30) == 1
    assert _count(session) == 4


def test_stream_stats_route(session: Session) -> None:
    record_stages(session, _processed_envelope())
    session.flush()
    session.commit()

    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    response = TestClient(app).get("/v1/stream/stats?hours=48")

    assert response.status_code == 200
    body = response.json()
    assert body["hours"] == 48
    stages = {bucket["stage"] for bucket in body["buckets"]}
    assert stages == {"produce", "parse", "categorize", "inject"}
