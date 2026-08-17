import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from researchscout.ingest.pipeline import run_ingest
from researchscout.schema import Author, Paper, SignalType
from researchscout.sources.base import RawItem
from researchscout.sources.s2_signal import SemanticScholarSource
from researchscout.store.papers import upsert_paper
from researchscout.store.signals import series

FIXTURE = Path(__file__).parent / "fixtures" / "s2_paper.json"
SINCE = datetime(2024, 1, 1, tzinfo=UTC)


def _paper(arxiv: str = "2401.00001", published: datetime = SINCE) -> Paper:
    return Paper(
        id=f"arxiv:{arxiv}",
        external_ids={"arxiv": arxiv},
        title="T",
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=published,
        source="arxiv",
    )


def _resp_class(status: int, body: object, retry_after: str | None = None) -> type:
    class _Resp:
        status_code = status
        is_success = 200 <= status < 300
        headers = {"Retry-After": retry_after} if retry_after else {}

        def raise_for_status(self) -> None:
            if status >= 400:
                raise httpx.HTTPStatusError(
                    str(status),
                    request=httpx.Request("POST", "https://x"),
                    response=httpx.Response(status),
                )

        def json(self) -> object:
            return body

    return _Resp


def test_normalize_builds_citation_signal() -> None:
    s2 = json.loads(FIXTURE.read_text())
    raw = RawItem(
        source="semantic_scholar",
        fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
        payload={"paper_id": "arxiv:2401.00001", **s2},
    )
    signal = SemanticScholarSource().normalize(raw)
    assert signal.paper_id == "arxiv:2401.00001"
    assert signal.type == SignalType.citation
    assert signal.source == "semantic_scholar"
    assert signal.value == 42.0
    assert signal.metadata["influential"] == 5


def test_fetch_batches_the_page_into_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole page is one POST, and null entries (unknown papers) are skipped in place."""
    src = SemanticScholarSource(api_key="k")
    monkeypatch.setattr(
        src,
        "_target_papers",
        lambda offset, limit: [
            ("arxiv:2401.00001", "2401.00001"),
            ("arxiv:2401.00002", "2401.00002"),
        ],
    )
    posted: list[dict[str, Any]] = []

    def capture(url: str, **kwargs: Any) -> object:
        posted.append(kwargs["json"])
        body = [
            {
                "citationCount": 3,
                "influentialCitationCount": 1,
                "externalIds": {"ArXiv": "2401.00001"},
            },
            None,
        ]
        return _resp_class(200, body)()

    monkeypatch.setattr(httpx, "post", capture)

    items, cursor = src.fetch(SINCE, None)
    assert posted == [{"ids": ["arXiv:2401.00001", "arXiv:2401.00002"]}]
    assert [item.payload["paper_id"] for item in items] == ["arxiv:2401.00001"]
    assert items[0].payload["citationCount"] == 3
    assert cursor is None  # a short page ends pagination


def test_fetch_survives_entries_dropped_from_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """A paper unknown to Semantic Scholar can vanish from the response instead of being null.

    The response is then shorter than the request and every later position is shifted, so
    counts must land on the papers whose arXiv id they carry — never on whoever happens to
    sit at the same index.
    """
    src = SemanticScholarSource(api_key="k")
    monkeypatch.setattr(
        src,
        "_target_papers",
        lambda offset, limit: [
            ("arxiv:2401.00001", "2401.00001"),
            ("arxiv:2401.00002", "2401.00002"),
            ("arxiv:2401.00003", "2401.00003"),
        ],
    )
    body = [
        {"citationCount": 3, "externalIds": {"ArXiv": "2401.00001"}},
        {"citationCount": 7, "externalIds": {"ArXiv": "2401.00003"}},
    ]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp_class(200, body)())

    items, _ = src.fetch(SINCE, None)
    assert [(i.payload["paper_id"], i.payload["citationCount"]) for i in items] == [
        ("arxiv:2401.00001", 3),
        ("arxiv:2401.00003", 7),
    ]


def test_fetch_skips_entries_without_an_arxiv_id(monkeypatch: pytest.MonkeyPatch) -> None:
    src = SemanticScholarSource(api_key="k")
    monkeypatch.setattr(
        src, "_target_papers", lambda offset, limit: [("arxiv:2401.00001", "2401.00001")]
    )
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _resp_class(200, [{"citationCount": 3, "externalIds": {}}])()
    )

    assert src.fetch(SINCE, None) == ([], None)


def test_batch_retries_the_rate_limit_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    src = SemanticScholarSource(api_key="k", sleep=sleeps.append)
    monkeypatch.setattr(src, "_target_papers", lambda offset, limit: [("arxiv:1", "1")])
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp_class(429, [], retry_after="9")())

    with pytest.raises(httpx.HTTPStatusError):
        src.fetch(SINCE, None)
    assert sleeps == [9.0, 9.0]  # Retry-After honored on both retries before giving up


def test_fetch_without_targets_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    src = SemanticScholarSource(api_key="k")
    monkeypatch.setattr(src, "_target_papers", lambda offset, limit: [])
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call expected"))
    )
    assert src.fetch(SINCE, None) == ([], None)


@pytest.mark.integration
def test_ingest_appends_citation_signal(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    upsert_paper(session, _paper())
    session.commit()  # so the source's read session sees the paper

    body = [json.loads(FIXTURE.read_text())]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp_class(200, body)())

    summary = run_ingest(session, SemanticScholarSource(), SINCE)
    assert summary.signals == 1

    points = series(session, "arxiv:2401.00001", "citation", SINCE)
    assert len(points) == 1
    assert points[0][1] == 42.0


@pytest.mark.integration
def test_ingest_skips_paper_unknown_to_s2(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown papers come back as null entries in the batch, not as 404s."""
    upsert_paper(session, _paper())
    session.commit()
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp_class(200, [None])())

    summary = run_ingest(session, SemanticScholarSource(), SINCE)
    assert summary.signals == 0
    assert series(session, "arxiv:2401.00001", "citation", SINCE) == []


@pytest.mark.integration
def test_targets_come_newest_first(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """A partial run should refresh the papers being ranked today, so newest go first."""
    upsert_paper(session, _paper("2401.00001", published=SINCE))
    upsert_paper(session, _paper("2406.00002", published=datetime(2024, 6, 1, tzinfo=UTC)))
    session.commit()
    posted: list[dict[str, Any]] = []

    def capture(url: str, **kwargs: Any) -> object:
        posted.append(kwargs["json"])
        return _resp_class(200, [None, None])()

    monkeypatch.setattr(httpx, "post", capture)

    run_ingest(session, SemanticScholarSource(), SINCE)
    assert posted[0]["ids"] == ["arXiv:2406.00002", "arXiv:2401.00001"]
