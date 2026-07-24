from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.orm import Session

from researchscout.ingest.pipeline import run_ingest
from researchscout.schema import Author, Paper, SignalType
from researchscout.sources.base import RawItem
from researchscout.sources.hf_trending import HuggingFaceTrendingSource
from researchscout.store.papers import upsert_paper
from researchscout.store.signals import series

SINCE = datetime(2024, 1, 1, tzinfo=UTC)
NOW = datetime(2024, 6, 1, tzinfo=UTC)


class _Resp:
    def __init__(self, status: int, body: object) -> None:
        self.status_code = status
        self.is_success = 200 <= status < 300
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._body


def _paper(canonical_id: str, arxiv_id: str) -> Paper:
    return Paper(
        id=canonical_id,
        external_ids={"arxiv": arxiv_id},
        title="T",
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=SINCE,
        source="arxiv",
    )


def test_normalize_builds_trending_signal() -> None:
    raw = RawItem(
        source="hf_trending",
        fetched_at=NOW,
        payload={"paper_id": "arxiv:2401.00001", "rank": 3, "upvotes": 42, "num_comments": 5},
    )
    signal = HuggingFaceTrendingSource().normalize(raw)
    assert signal.paper_id == "arxiv:2401.00001"
    assert signal.type == SignalType.hf_trending_rank
    assert signal.source == "hf_trending"
    assert signal.value == 3.0
    assert signal.metadata["upvotes"] == 42
    assert signal.metadata["num_comments"] == 5


@pytest.mark.integration
def test_fetch_records_rank_for_stored_paper(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    upsert_paper(session, _paper("arxiv:2401.00001", "2401.00001"))
    session.commit()  # so the source's read session sees the paper

    daily = [
        {"paper": {"id": "2401.00001", "upvotes": 10}, "numComments": 2},  # stored -> rank 1
        {"paper": {"id": "2402.99999", "upvotes": 99}, "numComments": 0},  # not stored -> skipped
    ]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, daily))

    summary = run_ingest(session, HuggingFaceTrendingSource(), SINCE)
    assert summary.signals == 1

    points = series(session, "arxiv:2401.00001", "hf_trending_rank", SINCE)
    assert len(points) == 1
    assert points[0][1] == 1.0


@pytest.mark.integration
def test_fetch_skips_papers_not_stored(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    daily = [{"paper": {"id": "9999.99999", "upvotes": 1}, "numComments": 0}]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, daily))

    summary = run_ingest(session, HuggingFaceTrendingSource(), SINCE)
    assert summary.signals == 0
