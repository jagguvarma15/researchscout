import math
from datetime import UTC, datetime, timedelta

import pytest

import researchscout.digest as digest_mod
from researchscout.digest import build_digest, rank_window, week_slug
from researchscout.llm.base import LLM
from researchscout.schema import Author, Paper

NOW = datetime.now(UTC)


class FakeLLM(LLM):
    model = "fake"

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.last_user = ""

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        self.last_user = user
        return self._reply


def _paper(pid: str, *, age_days: float) -> Paper:
    return Paper(
        id=pid,
        title=f"Paper {pid}",
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=NOW - timedelta(days=age_days),
        source="arxiv",
    )


def test_week_slug_is_iso_week() -> None:
    assert week_slug(datetime(2026, 7, 6, tzinfo=UTC)) == "2026-w28"


def test_breakthrough_outranks_freshness(monkeypatch: pytest.MonkeyPatch) -> None:
    fresh = _paper("arxiv:2401.00001", age_days=0.5)
    cited = _paper("arxiv:2401.00002", age_days=4.0)
    monkeypatch.setattr(digest_mod, "list_papers", lambda *a, **k: [fresh, cited])
    citations = {"arxiv:2401.00001": 0.0, "arxiv:2401.00002": 250.0}
    boosts = {"arxiv:2401.00001": 0.0, "arxiv:2401.00002": math.log1p(250.0)}
    monkeypatch.setattr(digest_mod, "_latest_citations", lambda s, pid: citations[pid])
    monkeypatch.setattr(digest_mod, "_breakthrough_boost", lambda s, pid: boosts[pid])

    ranked = rank_window(None, days=7, k=2)
    assert [r.paper.id for r in ranked] == ["arxiv:2401.00002", "arxiv:2401.00001"]
    assert ranked[0].citations == 250.0


def test_build_digest_post_checks_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    papers = [_paper("arxiv:2401.00001", age_days=1)]
    monkeypatch.setattr(digest_mod, "list_papers", lambda *a, **k: papers)
    monkeypatch.setattr(digest_mod, "_latest_citations", lambda *a: 0.0)
    monkeypatch.setattr(digest_mod, "_breakthrough_boost", lambda *a: 0.0)
    llm = FakeLLM("Big week [arxiv:2401.00001], also fake [arxiv:9999.99999].")

    digest = build_digest(None, llm, days=7, k=5)

    assert digest is not None
    assert digest.cited == ["arxiv:2401.00001"]  # the invented id is dropped
    assert digest.slug == week_slug(datetime.now(UTC))
    assert "Paper arxiv:2401.00001" in llm.last_user


def test_build_digest_empty_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(digest_mod, "list_papers", lambda *a, **k: [])
    assert build_digest(None, FakeLLM("unused"), days=7) is None
