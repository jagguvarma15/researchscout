import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from researchscout.ingest.pipeline import run_ingest
from researchscout.schema import Author, Paper, SignalType
from researchscout.sources.base import RawItem
from researchscout.sources.s2_signal import SemanticScholarSource
from researchscout.store.papers import upsert_paper
from researchscout.store.signals import series

FIXTURE = Path(__file__).parent / "fixtures" / "s2_paper.json"
SINCE = datetime(2024, 1, 1, tzinfo=UTC)


def _paper() -> Paper:
    return Paper(
        id="arxiv:2401.00001",
        external_ids={"arxiv": "2401.00001"},
        title="T",
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=SINCE,
        source="arxiv",
    )


def _resp_class(status: int, body: dict[str, object]) -> type:
    class _Resp:
        status_code = status
        is_success = 200 <= status < 300

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return body

    return _Resp


def test_normalize_builds_citation_signal() -> None:
    s2 = json.loads(FIXTURE.read_text())
    raw = RawItem(
        source="semantic_scholar",
        fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
        payload={"paper_id": "arxiv:2401.00001", **s2},
    )
    signal = SemanticScholarSource().normalize(raw)
    assert signal.paper_id == "arxiv:2401.00001"
    assert signal.type == SignalType.citation
    assert signal.source == "semantic_scholar"
    assert signal.value == 42.0
    assert signal.metadata["influential"] == 5


@pytest.mark.integration
def test_ingest_appends_citation_signal(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    upsert_paper(session, _paper())
    session.commit()  # so the source's read session sees the paper

    body = json.loads(FIXTURE.read_text())
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp_class(200, body)())

    summary = run_ingest(session, SemanticScholarSource(), SINCE)
    assert summary.signals == 1

    points = series(session, "arxiv:2401.00001", "citation", SINCE)
    assert len(points) == 1
    assert points[0][1] == 42.0


@pytest.mark.integration
def test_ingest_skips_paper_unknown_to_s2(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    upsert_paper(session, _paper())
    session.commit()
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _resp_class(404, {})())

    summary = run_ingest(session, SemanticScholarSource(), SINCE)
    assert summary.signals == 0
    assert series(session, "arxiv:2401.00001", "citation", SINCE) == []
