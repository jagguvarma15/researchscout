from datetime import UTC, datetime

import httpx
import pytest

from researchscout.schema import SignalType
from researchscout.sources.base import RawItem
from researchscout.sources.openalex import OpenAlexSource

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


def test_fetch_batches_dois_and_maps_back(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_filters: list[str] = []

    def fake_get(url: str, *, params: dict, **kwargs: object) -> _Resp:
        seen_filters.append(params["filter"])
        return _Resp(
            200,
            {
                "results": [
                    {"doi": "https://doi.org/10.48550/arxiv.2401.00001", "cited_by_count": 17},
                    {"doi": "https://doi.org/10.1234/unrelated", "cited_by_count": 99},
                ]
            },
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    source = OpenAlexSource()
    monkeypatch.setattr(
        source,
        "_stored_arxiv_ids",
        lambda: {"2401.00001": "arxiv:2401.00001", "2401.00002": "arxiv:2401.00002"},
    )

    items, cursor = source.fetch(SINCE, None)

    assert cursor is None
    assert seen_filters == ["doi:10.48550/arXiv.2401.00001|10.48550/arXiv.2401.00002"]
    assert len(items) == 1  # the unrelated DOI is dropped
    assert items[0].payload == {"paper_id": "arxiv:2401.00001", "cited_by_count": 17}


def test_mailto_joins_the_polite_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_params: list[dict] = []

    def fake_get(url: str, *, params: dict, **kwargs: object) -> _Resp:
        seen_params.append(params)
        return _Resp(200, {"results": []})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(
        "researchscout.sources.openalex.source_config", lambda name: {"mailto": "me@example.com"}
    )
    source = OpenAlexSource()
    monkeypatch.setattr(source, "_stored_arxiv_ids", lambda: {"2401.00001": "arxiv:2401.00001"})
    source.fetch(SINCE, None)
    assert seen_params[0]["mailto"] == "me@example.com"


def test_normalize_builds_citation_signal() -> None:
    signal = OpenAlexSource().normalize(
        RawItem(
            source="openalex",
            fetched_at=NOW,
            payload={"paper_id": "arxiv:2401.00001", "cited_by_count": 17},
        )
    )
    assert signal.type == SignalType.citation
    assert signal.source == "openalex"
    assert signal.value == 17.0
    assert signal.observed_at == NOW
