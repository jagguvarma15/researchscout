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


def _route(daily: object, per_paper: dict[str, object] | None = None):
    """Fake httpx.get: the daily feed for its URL, per-paper bodies by arXiv id, else 404."""

    def fake_get(url: str, *a: object, **k: object) -> _Resp:
        if url == "https://huggingface.co/api/daily_papers":
            return _Resp(200, daily)
        for arxiv_id, body in (per_paper or {}).items():
            if url.endswith(f"/api/papers/{arxiv_id}"):
                return _Resp(200, body)
        return _Resp(404, {})

    return fake_get


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
    monkeypatch.setattr(httpx, "get", _route(daily))
    monkeypatch.setattr("researchscout.sources.hf_trending.time.sleep", lambda s: None)

    summary = run_ingest(session, HuggingFaceTrendingSource(), SINCE)
    assert summary.signals == 1

    points = series(session, "arxiv:2401.00001", "hf_trending_rank", SINCE)
    assert len(points) == 1
    assert points[0][1] == 1.0


@pytest.mark.integration
def test_fetch_skips_papers_not_stored(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    daily = [{"paper": {"id": "9999.99999", "upvotes": 1}, "numComments": 0}]
    monkeypatch.setattr(httpx, "get", _route(daily))
    monkeypatch.setattr("researchscout.sources.hf_trending.time.sleep", lambda s: None)

    summary = run_ingest(session, HuggingFaceTrendingSource(), SINCE)
    assert summary.signals == 0


@pytest.mark.integration
def test_per_paper_upvotes_recorded_off_the_daily_list(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    upsert_paper(session, _paper("arxiv:2401.00001", "2401.00001"))
    session.commit()

    monkeypatch.setattr(
        httpx,
        "get",
        _route(daily=[], per_paper={"2401.00001": {"upvotes": 7, "numComments": 2}}),
    )
    monkeypatch.setattr("researchscout.sources.hf_trending.time.sleep", lambda s: None)

    summary = run_ingest(session, HuggingFaceTrendingSource(), SINCE)
    assert summary.signals == 2

    mentions = series(session, "arxiv:2401.00001", "social_mention", SINCE)
    discussion = series(session, "arxiv:2401.00001", "discussion", SINCE)
    assert [value for _, value in mentions] == [7.0]
    assert [value for _, value in discussion] == [2.0]


def test_normalize_maps_per_paper_metrics() -> None:
    source = HuggingFaceTrendingSource()
    upvotes = source.normalize(
        RawItem(
            source="hf_trending",
            fetched_at=NOW,
            payload={
                "paper_id": "arxiv:2401.00001",
                "metric": "upvotes",
                "value": 7,
                "arxiv_id": "2401.00001",
            },
        )
    )
    assert upvotes.type == SignalType.social_mention
    assert upvotes.value == 7.0

    comments = source.normalize(
        RawItem(
            source="hf_trending",
            fetched_at=NOW,
            payload={"paper_id": "arxiv:2401.00001", "metric": "comments", "value": 2},
        )
    )
    assert comments.type == SignalType.discussion
    assert comments.value == 2.0
