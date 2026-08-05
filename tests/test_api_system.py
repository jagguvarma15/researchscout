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
    assert body["migration"]  # whatever head the test database is migrated to
    assert body["runs"][0]["task"] == "ingest"
    assert body["runs"][0]["ok"] is True


def test_status_on_an_empty_corpus(session: Session) -> None:
    body = _client(session).get("/v1/system/status").json()
    assert body["papers"] == 0
    assert body["newest_paper_at"] is None
    assert body["runs"] == []
    assert body["version"]
    assert body["build_sha"] is None  # a source checkout carries no stamp
