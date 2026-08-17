from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

import researchscout.agentic as agentic_mod
from researchscout.agentic import follow_references
from researchscout.retrieve.search import ScoredPaper
from researchscout.schema import Author, Paper
from researchscout.store.citations import citing_ids_for, references_cached, store_references
from researchscout.store.papers import get_paper, upsert_paper

pytestmark = pytest.mark.integration


def _paper(
    arxiv: str,
    title: str = "T",
    published: datetime = datetime(2024, 1, 1, tzinfo=UTC),
) -> Paper:
    return Paper(
        id=f"arxiv:{arxiv}",
        external_ids={"arxiv": arxiv},
        title=title,
        abstract="a",
        authors=[Author(name="A")],
        categories=["cs.LG"],
        published_at=published,
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


def test_stalest_targets_walk_null_watermarks_first(session: Session) -> None:
    """Never-fetched papers lead, newest published first; stamped ones go to the back."""
    from researchscout.store.citations import mark_citations_refreshed, stalest_citation_targets

    for arxiv_id, day in (("2401.00001", 1), ("2401.00002", 2), ("2401.00003", 3)):
        upsert_paper(session, _paper(arxiv_id, published=datetime(2024, 1, day, tzinfo=UTC)))
    session.flush()

    targets = stalest_citation_targets(session, limit=10)
    assert [pid for pid, _ in targets] == ["arxiv:2401.00003", "arxiv:2401.00002", "arxiv:2401.00001"]

    mark_citations_refreshed(
        session,
        ["arxiv:2401.00003"],
        source="semantic_scholar",
        fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    targets = stalest_citation_targets(session, limit=10)
    assert [pid for pid, _ in targets] == ["arxiv:2401.00002", "arxiv:2401.00001", "arxiv:2401.00003"]


def test_fallback_targets_exclude_freshly_stamped_papers(session: Session) -> None:
    from researchscout.store.citations import mark_citations_refreshed, stale_fallback_targets

    for arxiv_id in ("2401.00001", "2401.00002"):
        upsert_paper(session, _paper(arxiv_id, published=datetime(2024, 1, 1, tzinfo=UTC)))
    session.flush()
    now = datetime(2024, 6, 15, tzinfo=UTC)
    mark_citations_refreshed(
        session, ["arxiv:2401.00001"], source="semantic_scholar", fetched_at=now
    )
    mark_citations_refreshed(
        session,
        ["arxiv:2401.00002"],
        source="semantic_scholar",
        fetched_at=now - timedelta(days=30),
    )

    stale = stale_fallback_targets(session, older_than=now - timedelta(days=7), limit=10)
    assert [pid for pid, _ in stale] == ["arxiv:2401.00002"]


def test_marking_again_replaces_the_watermark(session: Session) -> None:
    from researchscout.store.citations import mark_citations_refreshed
    from researchscout.store.models import CitationRefreshRow

    upsert_paper(session, _paper("2401.00001", published=datetime(2024, 1, 1, tzinfo=UTC)))
    session.flush()
    first = datetime(2024, 6, 1, tzinfo=UTC)
    mark_citations_refreshed(session, ["arxiv:2401.00001"], source="semantic_scholar", fetched_at=first)
    later = datetime(2024, 7, 1, tzinfo=UTC)
    mark_citations_refreshed(session, ["arxiv:2401.00001"], source="openalex", fetched_at=later)

    row = session.get(CitationRefreshRow, "arxiv:2401.00001")
    assert row is not None
    assert row.source == "openalex"
    assert row.fetched_at == later
