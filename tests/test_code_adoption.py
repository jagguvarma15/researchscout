from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.orm import Session

from researchscout.ingest.pipeline import run_ingest
from researchscout.schema import Author, Paper, SignalType
from researchscout.sources.base import RawItem
from researchscout.sources.code_adoption import GitHubCodeAdoptionSource
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


def test_normalize_builds_code_stars_signal() -> None:
    raw = RawItem(
        source="code_adoption",
        fetched_at=NOW,
        payload={
            "paper_id": "arxiv:2401.00001",
            "stars": 1234,
            "repo": "org/repo",
            "url": "https://github.com/org/repo",
        },
    )
    signal = GitHubCodeAdoptionSource().normalize(raw)
    assert signal.paper_id == "arxiv:2401.00001"
    assert signal.type == SignalType.code_stars
    assert signal.source == "code_adoption"
    assert signal.value == 1234.0
    assert signal.metadata["repo"] == "org/repo"


@pytest.mark.integration
def test_fetch_records_stars_for_stored_paper(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    upsert_paper(session, _paper("arxiv:2401.00001", "2401.00001"))
    session.commit()

    body = {
        "items": [
            {
                "stargazers_count": 321,
                "full_name": "org/repo",
                "html_url": "https://github.com/org/repo",
            }
        ]
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, body))

    summary = run_ingest(session, GitHubCodeAdoptionSource(), SINCE)
    assert summary.signals == 1

    points = series(session, "arxiv:2401.00001", "code_stars", SINCE)
    assert len(points) == 1
    assert points[0][1] == 321.0


@pytest.mark.integration
def test_fetch_skips_paper_without_repo(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    upsert_paper(session, _paper("arxiv:2401.00001", "2401.00001"))
    session.commit()
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, {"items": []}))

    summary = run_ingest(session, GitHubCodeAdoptionSource(), SINCE)
    assert summary.signals == 0
    assert series(session, "arxiv:2401.00001", "code_stars", SINCE) == []
