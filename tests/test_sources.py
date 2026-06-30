from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from researchscout.schema import Paper
from researchscout.sources import enabled_sources, get_source
from researchscout.sources.arxiv import ArxivSource, _entry_payload, _normalize_payload

FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_query.atom"


def _first_payload() -> dict[str, object]:
    import feedparser

    feed = feedparser.parse(FIXTURE.read_text())
    return _entry_payload(feed.entries[0])


def test_normalize_maps_canonical_fields() -> None:
    paper = _normalize_payload(_first_payload())
    assert isinstance(paper, Paper)
    assert paper.id == "arxiv:2401.12345"
    assert paper.external_ids == {"arxiv": "2401.12345"}
    assert paper.title == "A Great Paper on Transformers"
    assert paper.abstract.startswith("We present a great method.")
    assert [a.name for a in paper.authors] == ["Ada Lovelace", "Alan Turing"]
    assert "cs.LG" in paper.categories
    assert "cs.AI" in paper.categories
    assert paper.published_at == datetime(2024, 1, 23, 18, 30, tzinfo=UTC)
    assert paper.source == "arxiv"
    assert paper.pdf_url == "http://arxiv.org/pdf/2401.12345v2"


def test_normalize_strips_version() -> None:
    paper = _normalize_payload(_first_payload())
    assert paper.external_ids["arxiv"] == "2401.12345"


def test_search_query_format() -> None:
    src = ArxivSource(categories=["cs.LG", "cs.AI"])
    query = src._search_query(datetime(2024, 1, 1, tzinfo=UTC))
    assert "cat:cs.LG" in query
    assert "cat:cs.AI" in query
    assert "submittedDate:[202401010000 TO" in query


def test_get_source_returns_arxiv() -> None:
    src = get_source("arxiv")
    assert isinstance(src, ArxivSource)
    assert src.kind == "content"


def test_get_source_unknown_raises() -> None:
    with pytest.raises(KeyError):
        get_source("does-not-exist")


def test_enabled_sources_respects_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "sources.yaml"
    monkeypatch.setenv("RS_SOURCES_CONFIG_PATH", str(cfg))

    cfg.write_text("sources:\n  arxiv:\n    enabled: false\n    kind: content\n")
    assert all(s.name != "arxiv" for s in enabled_sources())

    cfg.write_text("sources:\n  arxiv:\n    enabled: true\n    kind: content\n")
    assert "arxiv" in [s.name for s in enabled_sources("content")]


def test_fetch_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def __init__(self) -> None:
            self.text = FIXTURE.read_text()
            self.status_code = 200
            self.is_success = True

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())

    items, cursor = ArxivSource(page_size=1).fetch(datetime(2024, 1, 1, tzinfo=UTC), None)
    assert len(items) == 1
    assert cursor == "1"  # full page → more may exist

    _, exhausted = ArxivSource(page_size=10).fetch(datetime(2024, 1, 1, tzinfo=UTC), None)
    assert exhausted is None  # partial page → done
