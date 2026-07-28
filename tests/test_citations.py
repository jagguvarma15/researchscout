from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

import researchscout.agentic as agentic_mod
from researchscout.agentic import follow_references
from researchscout.retrieve.search import ScoredPaper
from researchscout.schema import Author, Paper
from researchscout.store.citations import citing_ids_for, references_cached, store_references
from researchscout.store.papers import get_paper, upsert_paper

pytestmark = pytest.mark.integration


def _paper(arxiv: str, title: str = "T") -> Paper:
    return Paper(
        id=f"arxiv:{arxiv}",
        external_ids={"arxiv": arxiv},
        title=title,
        abstract="a",
        authors=[Author(name="A")],
        categories=["cs.LG"],
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source="arxiv",
    )


def _scored(session: Session, arxiv: str) -> ScoredPaper:
    paper = get_paper(session, f"arxiv:{arxiv}")
    assert paper is not None
    return ScoredPaper(paper=paper, score=1.0, distance=0.0)


def test_reference_cache_roundtrip(session: Session) -> None:
    upsert_paper(session, _paper("2401.00001"))
    session.flush()
    assert references_cached(session, "arxiv:2401.00001") is None  # never fetched

    store_references(session, "arxiv:2401.00001", ["2301.00002", "2301.00003", "2301.00002"])
    assert references_cached(session, "arxiv:2401.00001") == ["2301.00002", "2301.00003"]
    assert citing_ids_for(session, "2301.00002") == ["arxiv:2401.00001"]

    # A re-fetch is idempotent and unions edges (references only ever accrue).
    store_references(session, "arxiv:2401.00001", ["2301.00002"])
    assert references_cached(session, "arxiv:2401.00001") == ["2301.00002", "2301.00003"]


def test_empty_fetch_is_cached_as_no_references(session: Session) -> None:
    upsert_paper(session, _paper("2401.00001"))
    session.flush()
    store_references(session, "arxiv:2401.00001", [])
    assert references_cached(session, "arxiv:2401.00001") == []


def test_follow_references_fetches_once_then_reads_the_cache(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    upsert_paper(session, _paper("2401.00001"))
    upsert_paper(session, _paper("2301.00002"))
    session.flush()
    calls: list[str] = []

    def fake_refs(arxiv_id: str, *, limit: int) -> list[str]:
        calls.append(arxiv_id)
        return ["2301.00002"]

    monkeypatch.setattr(agentic_mod, "_reference_arxiv_ids", fake_refs)
    first = follow_references(session, [_scored(session, "2401.00001")])
    assert [item.paper.id for item in first] == ["arxiv:2301.00002"]
    assert calls == ["2401.00001"]

    second = follow_references(session, [_scored(session, "2401.00001")])
    assert [item.paper.id for item in second] == ["arxiv:2301.00002"]
    assert calls == ["2401.00001"]  # cache hit: no second lookup


def test_failed_fetch_is_not_cached(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    upsert_paper(session, _paper("2401.00001"))
    session.flush()
    monkeypatch.setattr(agentic_mod, "_reference_arxiv_ids", lambda a, *, limit: None)
    assert follow_references(session, [_scored(session, "2401.00001")]) == []
    assert references_cached(session, "arxiv:2401.00001") is None
