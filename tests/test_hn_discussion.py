from datetime import UTC, datetime

import httpx
import pytest

from researchscout.schema import SignalType
from researchscout.sources.base import RawItem
from researchscout.sources.hn_discussion import _ARXIV_RE, HackerNewsDiscussionSource

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


def test_arxiv_link_shapes_are_recognized() -> None:
    for url in (
        "https://arxiv.org/abs/2401.00001",
        "http://arxiv.org/pdf/2401.00001v2",
        "https://arxiv.org/html/2401.00001",
        "https://ARXIV.org/abs/2401.00001v3",
    ):
        match = _ARXIV_RE.search(url)
        assert match is not None and match.group(1) == "2401.00001"
    assert _ARXIV_RE.search("https://example.com/2401.00001") is None


def test_old_style_arxiv_ids_are_recognized() -> None:
    """Old papers keep resurfacing on HN, and their ids look nothing like the new form."""
    for url, expected in (
        ("https://arxiv.org/abs/math/0309136", "math/0309136"),
        ("https://arxiv.org/pdf/cond-mat.str-el/0309136v2", "cond-mat.str-el/0309136"),
        ("https://arxiv.org/abs/quant-ph/9508027", "quant-ph/9508027"),
    ):
        match = _ARXIV_RE.search(url)
        assert match is not None and match.group(1) == expected


def test_fetch_aggregates_stories_and_emits_both_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits = [
        {
            "objectID": "1",
            "url": "https://arxiv.org/abs/2401.00001",
            "title": "A paper",
            "points": 120,
            "num_comments": 40,
        },
        {
            "objectID": "2",
            "url": None,
            "title": "Show HN: arxiv.org/abs/2401.00001v2 reader",
            "points": 10,
            "num_comments": 3,
        },
        {
            "objectID": "3",
            "url": "https://arxiv.org/abs/2401.99999",  # not in the store
            "title": "Unknown",
            "points": 5,
            "num_comments": 1,
        },
        {
            "objectID": "4",
            "url": "https://example.com/blog",  # no arXiv link
            "title": "Something else",
            "points": 50,
            "num_comments": 9,
        },
    ]
    body = {"hits": hits, "nbPages": 1}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, body))
    source = HackerNewsDiscussionSource()
    monkeypatch.setattr(source, "_match_stored", lambda ids: {"2401.00001": "arxiv:2401.00001"})

    items, cursor = source.fetch(SINCE, None)

    assert cursor is None
    payloads = {item.payload["metric"]: item.payload for item in items}
    assert set(payloads) == {"points", "comments"}
    assert payloads["points"]["value"] == 130  # both stories summed
    assert payloads["comments"]["value"] == 43
    assert payloads["points"]["stories"] == ["1", "2"]
    assert all(item.payload["paper_id"] == "arxiv:2401.00001" for item in items)


def test_fetch_stops_at_reported_page_count(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_get(url: str, *, params: dict, **kwargs: object) -> _Resp:
        calls.append(params["page"])
        return _Resp(200, {"hits": [{"objectID": str(params["page"])}], "nbPages": 3})

    monkeypatch.setattr(httpx, "get", fake_get)
    source = HackerNewsDiscussionSource()
    monkeypatch.setattr(source, "_match_stored", lambda ids: {})
    source.fetch(SINCE, None)
    assert calls == [0, 1, 2]


def test_normalize_maps_points_and_comments() -> None:
    source = HackerNewsDiscussionSource()
    points = source.normalize(
        RawItem(
            source="hn_discussion",
            fetched_at=NOW,
            payload={
                "paper_id": "arxiv:2401.00001",
                "metric": "points",
                "value": 130,
                "stories": ["1", "2"],
            },
        )
    )
    assert points.type == SignalType.social_mention
    assert points.value == 130.0
    assert points.metadata["stories"] == ["1", "2"]
    assert points.observed_at == NOW

    comments = source.normalize(
        RawItem(
            source="hn_discussion",
            fetched_at=NOW,
            payload={"paper_id": "arxiv:2401.00001", "metric": "comments", "value": 43},
        )
    )
    assert comments.type == SignalType.discussion
    assert comments.value == 43.0
