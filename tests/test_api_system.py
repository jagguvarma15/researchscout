"""The system status route — deployment truth over HTTP.

Public like /sources; make deploy-verify and the footer freshness line both depend on the
exact field names pinned here.
"""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from researchscout.api.deps import get_session
from researchscout.api.main import create_app
from researchscout.schema import Author, Paper
from researchscout.store.papers import upsert_paper
from researchscout.store.runs import record_run

pytestmark = pytest.mark.integration


def _client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def test_status_reports_corpus_and_runs(
    session: Session, set_setting: Callable[[str, str], None]
) -> None:
    set_setting("RS_BUILD_SHA", "abc1234")
    set_setting("RS_SCHEDULER_PIPELINE_AT", "05:00,10:00,14:00,17:00")
    upsert_paper(
        session,
        Paper(
            id="arxiv:2401.00001",
            external_ids={"arxiv": "2401.00001"},
            title="T",
            abstract="A",
            authors=[Author(name="X")],
            categories=["cs.LG"],
            published_at=datetime(2026, 8, 1, tzinfo=UTC),
            source="arxiv",
        ),
    )
    record_run(
        session,
        "scheduler",
        started_at=datetime(2026, 8, 5, 8, 59, tzinfo=UTC),
        finished_at=datetime(2026, 8, 5, 8, 59, tzinfo=UTC),
        ok=True,
        note="started: 8 task(s)",
    )
    record_run(
        session,
        "ingest",
        started_at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
        finished_at=datetime(2026, 8, 5, 9, 1, tzinfo=UTC),
        ok=True,
    )
    session.commit()

    body = _client(session).get("/v1/system/status").json()
    assert body["build_sha"] == "abc1234"
    assert body["papers"] == 1
    assert body["newest_paper_at"].startswith("2026-08-01")
    # created_at is stamped by the database at insert, so pin presence and recency shape.
    assert body["newest_paper_created_at"] is not None
    assert body["migration"]  # whatever head the test database is migrated to
    assert body["runs"][0]["task"] == "ingest"
    assert body["runs"][0]["ok"] is True
    # Four slots a day means some slot is always in the past; the exact one depends on when
    # the test runs, so pin presence rather than a value.
    assert body["pipeline_due_at"] is not None
    assert body["scheduler_started_at"].startswith("2026-08-05")
    names = [check["name"] for check in body["health"]]
    assert "pipeline_runs" in names
    assert "corpus_freshness" in names
    groups = {group["group"]: group for group in body["schedule"]}
    assert groups["pipeline"]["at"] == ["05:00", "10:00", "14:00", "17:00"]
    assert groups["pipeline"]["next_run"] is not None


def test_status_carries_running_rows_and_the_last_health_run(session: Session) -> None:
    from researchscout.store.runs import record_task_started

    record_run(
        session,
        "health",
        started_at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
        finished_at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
        ok=True,
        note="freshness=ok",
    )
    record_task_started(session, "fulltext", started_at=datetime(2026, 8, 5, 10, 0, tzinfo=UTC))
    session.commit()

    body = _client(session).get("/v1/system/status").json()
    assert body["runs"][0]["task"] == "fulltext"
    assert body["runs"][0]["finished_at"] is None
    assert body["last_health_run"]["note"] == "freshness=ok"


def test_status_on_an_empty_corpus(session: Session) -> None:
    body = _client(session).get("/v1/system/status").json()
    assert body["papers"] == 0
    assert body["newest_paper_at"] is None
    assert body["runs"] == []
    assert body["version"]
    assert body["build_sha"] is None  # a source checkout carries no stamp
    assert body["pipeline_due_at"] is None  # interval schedule: no slot to be due
    assert body["scheduler_started_at"] is None


def test_status_reports_ask_usage(session: Session) -> None:
    from researchscout.store.ask_metrics import record_ask

    # Quiet deployment: no questions means no ask block at all.
    body = _client(session).get("/v1/system/status").json()
    assert body["ask"] is None

    record_ask(
        session,
        mode="fast",
        surface="chat",
        question="what beats transformers?",
        retrieved=3,
        best_relevance=0.9,
        found=True,
        retrieve_ms=50,
        rerank_ms=None,
        llm_ms=None,
        total_ms=120,
    )
    record_ask(
        session,
        mode="fast",
        surface="chat",
        question="obscure thing nobody wrote about",
        retrieved=0,
        best_relevance=0.1,
        found=False,
        retrieve_ms=40,
        rerank_ms=None,
        llm_ms=None,
        total_ms=90,
    )
    session.commit()

    body = _client(session).get("/v1/system/status").json()
    assert body["ask"]["asked"] == 2
    assert body["ask"]["found_rate"] == pytest.approx(0.5)
    assert body["ask"]["fast_p50_ms"] is not None
    assert body["ask"]["notfound"] == ["obscure thing nobody wrote about"]
    assert body["ask"]["refused"] == 0 and body["ask"]["llm_errors"] == 0


def test_status_reports_feed_usage(session: Session) -> None:
    from researchscout.store.feed_metrics import record_feed

    # Quiet deployment: no renders means no feed block.
    body = _client(session).get("/v1/system/status").json()
    assert body["feed"] is None

    for total_ms, hit in ((80, True), (120, True), (300, False)):
        record_feed(
            session,
            user_hash="abc123def456",
            days=30,
            k=20,
            centroids=3,
            candidates=100,
            returned=20,
            profile_cache_hit=hit,
            profile_ms=2,
            search_ms=30,
            signals_ms=20,
            rank_ms=8,
            total_ms=total_ms,
        )
    session.commit()

    body = _client(session).get("/v1/system/status").json()
    assert body["feed"]["requests"] == 3
    assert body["feed"]["p50_ms"] is not None
    assert body["feed"]["cache_hit_rate"] == pytest.approx(2 / 3)


def test_status_reports_catalog_freshness(session: Session) -> None:
    from researchscout.store import catalog
    from researchscout.store.catalog import ModelUpsert

    # Quiet deployment: an empty catalogue means no catalog block.
    body = _client(session).get("/v1/system/status").json()
    assert body["catalog"] is None

    catalog.upsert_models(
        session, [ModelUpsert(name="M", organization="OpenAI", source="epoch_ai")]
    )
    session.commit()

    body = _client(session).get("/v1/system/status").json()
    assert body["catalog"]["models_at"] is not None
    assert body["catalog"]["as_of"] is not None
    assert body["catalog"]["benchmarks_at"] is None


def test_status_reports_llm_usage(session: Session) -> None:
    from researchscout.llm.usage import LlmCallUsage
    from researchscout.store.llm_usage import add_usage

    # Quiet deployment: no model calls means no llm block at all.
    body = _client(session).get("/v1/system/status").json()
    assert body["llm"] is None

    add_usage(
        session,
        LlmCallUsage(
            purpose="synthesis",
            model="test-model",
            prompt_tokens=1200,
            completion_tokens=300,
            latency_ms=900,
            outcome="ok",
            detail=None,
        ),
    )
    add_usage(
        session,
        LlmCallUsage(
            purpose="topic_label",
            model="test-model",
            prompt_tokens=None,
            completion_tokens=None,
            latency_ms=100,
            outcome="quota",
            detail="429",
        ),
    )
    session.commit()

    body = _client(session).get("/v1/system/status").json()
    llm = body["llm"]
    assert llm["calls_today"] == 2
    assert llm["prompt_tokens_today"] == 1200
    assert llm["completion_tokens_today"] == 300
    assert llm["last_quota_at"] is not None
    purposes = {entry["purpose"]: entry for entry in llm["by_purpose"]}
    assert purposes["synthesis"]["ok"] == 1
    assert purposes["topic_label"]["quota"] == 1
