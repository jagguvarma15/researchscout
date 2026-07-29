from datetime import UTC, datetime

import httpx
import pytest

from researchscout.schema import SignalType
from researchscout.sources.base import RawItem
from researchscout.sources.openreview import OpenReviewSource, _rating_of

SINCE = datetime(2024, 1, 1, tzinfo=UTC)
NOW = datetime(2024, 6, 1, tzinfo=UTC)

TITLE = "Dynamic Diffusion Transformers"


class _Resp:
    def __init__(self, status: int, body: object) -> None:
        self.status_code = status
        self.is_success = 200 <= status < 300
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._body


def _submission(forum: str, title: str) -> dict:
    return {
        "id": forum,
        "forum": forum,
        "invitations": ["ICLR.cc/2026/Conference/-/Submission"],
        "content": {"title": {"value": title}},
    }


def _review(forum: str, rating: object) -> dict:
    return {
        "forum": forum,
        "invitations": ["ICLR.cc/2026/Conference/Submission1/-/Official_Review"],
        "content": {"rating": {"value": rating}},
    }


def test_rating_parses_bare_and_prefixed_forms() -> None:
    assert _rating_of({"content": {"rating": {"value": 6}}}) == 6.0
    assert _rating_of({"content": {"rating": "8: accept"}}) == 8.0
    assert _rating_of({"content": {"rating": {"value": "6: marginally above"}}}) == 6.0
    assert _rating_of({"content": {}}) is None


def test_fetch_scores_only_the_matching_forum(monkeypatch: pytest.MonkeyPatch) -> None:
    notes = [
        _submission("fA", TITLE),
        _submission("fB", "A Different Paper"),
        _review("fA", 6),
        _review("fA", "8: accept"),
        _review("fB", 2),  # other forum: excluded
    ]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, {"notes": notes}))
    monkeypatch.setattr("researchscout.sources.openreview.time.sleep", lambda seconds: None)
    source = OpenReviewSource()
    monkeypatch.setattr(source, "_recent_stored", lambda since: [("arxiv:2401.00001", TITLE)])

    items, _ = source.fetch(SINCE, None)

    assert len(items) == 1
    assert items[0].payload == {"paper_id": "arxiv:2401.00001", "score": 7.0}


def test_fetch_emits_nothing_without_an_exact_title_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = [_submission("fB", "A Different Paper"), _review("fB", 9)]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, {"notes": notes}))
    monkeypatch.setattr("researchscout.sources.openreview.time.sleep", lambda seconds: None)
    source = OpenReviewSource()
    monkeypatch.setattr(source, "_recent_stored", lambda since: [("arxiv:2401.00001", TITLE)])
    items, _ = source.fetch(SINCE, None)
    assert items == []


def test_fetch_stops_on_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_get(url: str, *, params: dict, **kwargs: object) -> _Resp:
        calls.append(params["term"])
        return _Resp(403, {"name": "ChallengeRequiredError"})

    monkeypatch.setattr(httpx, "get", fake_get)
    source = OpenReviewSource()
    monkeypatch.setattr(
        source,
        "_recent_stored",
        lambda since: [("arxiv:2401.00001", TITLE), ("arxiv:2401.00002", "Another")],
    )
    items, _ = source.fetch(SINCE, None)
    assert items == []
    assert calls == [TITLE]  # the walk stops at the first challenge


def test_normalize_builds_review_signal() -> None:
    signal = OpenReviewSource().normalize(
        RawItem(
            source="openreview",
            fetched_at=NOW,
            payload={"paper_id": "arxiv:2401.00001", "score": 7.0},
        )
    )
    assert signal.type == SignalType.review_score
    assert signal.source == "openreview"
    assert signal.value == 7.0
    assert signal.observed_at == NOW
