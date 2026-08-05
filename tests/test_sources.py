from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

import researchscout.sources.arxiv as arxiv
from researchscout.schema import Paper
from researchscout.sources import enabled_sources, get_source
from researchscout.sources.arxiv import ArxivSource, _entry_payload, _normalize_payload
from researchscout.sources.base import describe_sources, registered_sources, retry_wait
from researchscout.useragent import USER_AGENT

FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_query.atom"


MATH_FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_query_math.atom"


def _first_payload(fixture: Path = FIXTURE) -> dict[str, object]:
    import feedparser

    feed = feedparser.parse(fixture.read_text())
    return _entry_payload(feed.entries[0])


def test_normalize_maps_canonical_fields() -> None:
    paper = _normalize_payload(_first_payload())
    assert isinstance(paper, Paper)
    assert paper.id == "arxiv:2401.12345"
    assert paper.external_ids == {"arxiv": "2401.12345", "doi": "10.1000/example.2024"}
    assert paper.title == "A Great Paper on Transformers"
    assert paper.abstract.startswith("We present a great method.")
    assert [a.name for a in paper.authors] == ["Ada Lovelace", "Alan Turing"]
    assert "cs.LG" in paper.categories
    assert "cs.AI" in paper.categories
    assert paper.published_at == datetime(2024, 1, 23, 18, 30, tzinfo=UTC)
    assert paper.source == "arxiv"
    assert paper.pdf_url == "http://arxiv.org/pdf/2401.12345v2"


def test_normalize_maps_enriched_fields() -> None:
    paper = _normalize_payload(_first_payload())
    assert paper.primary_category == "cs.LG"
    assert paper.comment == "14 pages, 3 figures, accepted at NeurIPS 2024"
    assert paper.venue == "Advances in Neural Information Processing Systems 37 (2024)"


def test_normalize_keeps_latex_and_paragraphs() -> None:
    paper = _normalize_payload(_first_payload(MATH_FIXTURE))
    assert paper.title == "X$^3$-Attention: Fast $O(n \\log n)$ Kernels for $n \\geq 10^6$"
    assert "\n\n" in paper.abstract
    first, second = paper.abstract.split("\n\n")
    assert first == (
        "We study kernels with complexity $O(n \\log n)$ and error bounded by $\\epsilon \\geq 0$."
    )
    assert second == (
        "Our second contribution is a bound of the form $x_i^2 \\leq \\sum_j w_j$ over all inputs."
    )
    assert paper.primary_category == "math.OC"


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


class _Resp:
    def __init__(self) -> None:
        self.text = FIXTURE.read_text()
        self.status_code = 200
        self.is_success = True

    def raise_for_status(self) -> None:
        return None


def test_fetch_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    monkeypatch.setenv("RS_ARXIV_PAGE_DELAY_SEC", "0")

    items, cursor = ArxivSource(page_size=1).fetch(datetime(2024, 1, 1, tzinfo=UTC), None)
    assert len(items) == 1
    assert cursor == "1"  # full page → more may exist

    _, exhausted = ArxivSource(page_size=10).fetch(datetime(2024, 1, 1, tzinfo=UTC), None)
    assert exhausted is None  # partial page → done


def test_fetch_paces_every_request(
    monkeypatch: pytest.MonkeyPatch, set_setting: Callable[[str, str], None]
) -> None:
    """arXiv asks for one request every three seconds, so the floor spans fetches.

    Paging alone is not enough: a second category's first page, or a health probe, would
    otherwise follow the previous request immediately. The clock is per process, which is
    why the test resets it and drives a fake one.
    """
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    # set_setting rather than setenv: the delay is read from cached settings, so changing it
    # part-way through the test has to drop the cache the way a restart would.
    set_setting("RS_ARXIV_PAGE_DELAY_SEC", "2.5")
    monkeypatch.setattr(arxiv, "_last_request_at", None)
    sleeps: list[float] = []
    now = [100.0]
    src = ArxivSource(page_size=1, sleep=sleeps.append, clock=lambda: now[0])

    src.fetch(datetime(2024, 1, 1, tzinfo=UTC), None)
    assert sleeps == []  # nothing to wait for: the first request of the process

    src.fetch(datetime(2024, 1, 1, tzinfo=UTC), "1")
    assert sleeps == [2.5]  # next page, no time elapsed: the whole floor

    now[0] += 1.0
    other = ArxivSource(page_size=1, sleep=sleeps.append, clock=lambda: now[0])
    other.fetch(datetime(2024, 1, 1, tzinfo=UTC), None)
    assert sleeps == [2.5, 1.5]  # a fresh source's first page waits out the remainder

    set_setting("RS_ARXIV_PAGE_DELAY_SEC", "0")
    src.fetch(datetime(2024, 1, 1, tzinfo=UTC), "2")
    assert sleeps == [2.5, 1.5]  # zero disables the pause


class _RateLimited:
    status_code = 429
    is_success = False

    def __init__(self, retry_after: str | None = None) -> None:
        self.headers = {"Retry-After": retry_after} if retry_after else {}

    def raise_for_status(self) -> None:
        raise httpx.HTTPStatusError(
            "429", request=httpx.Request("GET", "https://x"), response=httpx.Response(429)
        )


def test_fetch_retries_a_rate_limit_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    responses: list[object] = [_RateLimited("7"), _Resp()]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: responses.pop(0))
    monkeypatch.setenv("RS_ARXIV_PAGE_DELAY_SEC", "0")
    sleeps: list[float] = []

    items, _cursor = ArxivSource(page_size=10, sleep=sleeps.append).fetch(
        datetime(2024, 1, 1, tzinfo=UTC), None
    )
    assert len(items) == 1  # the page parsed fine once arXiv stopped shedding load
    assert sleeps == [7.0]  # and the wait was the one arXiv asked for


def test_fetch_gives_up_after_bounded_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def limited(*args: object, **kwargs: object) -> _RateLimited:
        calls["n"] += 1
        return _RateLimited()

    monkeypatch.setattr(httpx, "get", limited)
    monkeypatch.setenv("RS_ARXIV_PAGE_DELAY_SEC", "0")
    sleeps: list[float] = []

    with pytest.raises(httpx.HTTPStatusError):
        ArxivSource(page_size=1, sleep=sleeps.append).fetch(datetime(2024, 1, 1, tzinfo=UTC), None)
    assert calls["n"] == 3  # the first attempt plus two retries, then the error surfaces
    assert sleeps == [15.0, 30.0]  # the doubling fallback when no Retry-After arrives


def test_retry_wait_honors_and_caps_the_header() -> None:
    assert retry_wait("7", 0, cap=120.0) == 7.0
    assert retry_wait("600", 0, cap=120.0) == 120.0  # an hour-long ask is not worth holding
    assert retry_wait("soon", 0, cap=120.0) == 15.0  # unparseable reads as absent
    assert retry_wait(None, 1, cap=20.0) == 20.0  # the fallback respects the cap too


def test_fetch_identifies_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """The /about page claims requests are identified; this is that claim."""
    seen: dict[str, str] = {}

    def capture(*args: object, **kwargs: object) -> _Resp:
        seen.update(kwargs.get("headers") or {})  # type: ignore[arg-type]
        return _Resp()

    monkeypatch.setattr(httpx, "get", capture)
    monkeypatch.setenv("RS_ARXIV_PAGE_DELAY_SEC", "0")
    ArxivSource(page_size=1).fetch(datetime(2024, 1, 1, tzinfo=UTC), None)

    assert seen["User-Agent"] == USER_AGENT
    assert seen["User-Agent"].startswith("researchscout/")
    assert "https://github.com/" in seen["User-Agent"]


def test_every_source_declares_attribution() -> None:
    """A connector cannot ship without saying where its data comes from."""
    described = describe_sources()
    registered = [cls.name for cls in registered_sources()]
    assert [d.name for d in described][: len(registered)] == registered

    for source in described:
        attribution = source.attribution
        assert attribution is not None, f"{source.name} has no attribution block"
        assert attribution.name
        assert attribution.provides
        assert attribution.data_license
        for url in (attribution.homepage, attribution.terms):
            assert url.startswith("https://"), f"{source.name}: {url}"


def test_catalog_sources_reach_the_listing_without_a_connector_class() -> None:
    """The model and benchmark upstreams have nothing to normalize into a Paper or a Signal.

    They are still somebody else's data being republished under a licence that requires credit,
    so they declare attribution in the same file and reach /about by the same route. Without
    this they would be the one set of sources the page does not mention.
    """
    described = {source.name: source for source in describe_sources()}
    for name in ("epoch_ai", "huggingface_models"):
        source = described[name]
        assert source.kind == "catalog"
        assert source.enabled is True
        assert source.attribution is not None
    # The licence Epoch AI publishes under asks for exactly this credit.
    epoch = described["epoch_ai"].attribution
    assert epoch is not None and "CC BY" in epoch.data_license
