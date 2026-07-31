"""Unit tests for the external web search (all HTTP faked)."""

import json
from typing import Any

import httpx
import pytest

from researchscout.websearch import WebHit, search_arxiv, search_s2, web_search

_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2607.11111v1</id>
    <title>Sparse  Attention\n Revisited</title>
    <summary>We revisit sparse attention for long contexts.</summary>
    <author><name>A. One</name></author>
    <author><name>B. Two</name></author>
    <published>2026-07-01T00:00:00Z</published>
    <updated>2026-07-02T00:00:00Z</updated>
  </entry>
</feed>
"""

_S2_BODY = {
    "total": 2,
    "data": [
        {
            "paperId": "s2one",
            "title": "Sparse Attention Revisited",
            "abstract": "Same paper, found by S2 too.",
            "year": 2026,
            "authors": [{"authorId": "1", "name": "A. One"}],
            "externalIds": {"ArXiv": "2607.11111"},
            "url": "https://www.semanticscholar.org/paper/s2one",
        },
        {
            "paperId": "s2two",
            "title": "A Non arXiv Result",
            "abstract": None,
            "year": 2025,
            "authors": [],
            "externalIds": {},
            "url": "https://www.semanticscholar.org/paper/s2two",
        },
    ],
}


class _FakeResponse:
    def __init__(self, *, text: str = "", body: dict[str, Any] | None = None) -> None:
        self.text = text
        self._body = body or {}
        self.status_code = 200

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        return None


def _fake_get(responses: dict[str, _FakeResponse]) -> Any:
    def get(url: str, **kwargs: Any) -> _FakeResponse:
        for fragment, response in responses.items():
            if fragment in url:
                return response
        raise AssertionError(f"unexpected url {url}")

    return get


def test_search_arxiv_parses_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", _fake_get({"export.arxiv.org": _FakeResponse(text=_ATOM)}))
    hits = search_arxiv("sparse attention")
    assert hits == [
        WebHit(
            provider="arxiv",
            title="Sparse Attention Revisited",
            authors=["A. One", "B. Two"],
            year=2026,
            snippet="We revisit sparse attention for long contexts.",
            arxiv_id="2607.11111",
            url="http://arxiv.org/abs/2607.11111v1",
        )
    ]


def test_search_s2_parses_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "get", _fake_get({"semanticscholar.org": _FakeResponse(body=_S2_BODY)})
    )
    hits = search_s2("sparse attention")
    assert [hit.arxiv_id for hit in hits] == ["2607.11111", None]
    assert hits[1].snippet == ""  # a null abstract never crashes


def test_web_search_merges_and_dedupes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "get",
        _fake_get(
            {
                "export.arxiv.org": _FakeResponse(text=_ATOM),
                "semanticscholar.org": _FakeResponse(body=_S2_BODY),
            }
        ),
    )
    hits, failed = web_search("sparse attention")
    assert failed == []
    # The shared arXiv id deduped; the arXiv-provider hit won the slot.
    assert [(hit.provider, hit.arxiv_id) for hit in hits] == [
        ("arxiv", "2607.11111"),
        ("s2", None),
    ]


def test_web_search_survives_a_failing_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def get(url: str, **kwargs: Any) -> _FakeResponse:
        if "semanticscholar.org" in url:
            raise httpx.ConnectError("down")
        return _FakeResponse(text=_ATOM)

    monkeypatch.setattr(httpx, "get", get)
    hits, failed = web_search("sparse attention")
    assert failed == ["s2"]
    assert [hit.provider for hit in hits] == ["arxiv"]


def test_payload_shapes_stay_json_serializable() -> None:
    hit = WebHit(
        provider="arxiv",
        title="t",
        authors=[],
        year=None,
        snippet="s",
        arxiv_id=None,
        url=None,
    )
    assert json.loads(json.dumps(hit.__dict__))["provider"] == "arxiv"
