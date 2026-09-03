import math
from datetime import UTC, datetime, timedelta

import pytest

import researchscout.digest as digest_mod
from researchscout.digest import build_digest, compose_digest, rank_window, week_slug
from researchscout.llm.base import LLM
from researchscout.schema import Author, Paper
from researchscout.score import Breakthrough

NOW = datetime.now(UTC)


class FakeLLM(LLM):
    model = "fake"

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.last_user = ""

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        self.last_user = user
        return self._reply


def _paper(pid: str, *, age_days: float, citation_count: int = 0) -> Paper:
    return Paper(
        id=pid,
        title=f"Paper {pid}",
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=NOW - timedelta(days=age_days),
        source="arxiv",
        citation_count=citation_count,
    )


def _boosts(totals: dict[str, float]) -> dict[str, Breakthrough]:
    return {
        pid: Breakthrough(total=total, contributions={"citation": total} if total else {})
        for pid, total in totals.items()
    }


def test_week_slug_is_iso_week() -> None:
    assert week_slug(datetime(2026, 7, 6, tzinfo=UTC)) == "2026-w28"


def test_breakthrough_outranks_freshness(monkeypatch: pytest.MonkeyPatch) -> None:
    fresh = _paper("arxiv:2401.00001", age_days=0.5)
    cited = _paper("arxiv:2401.00002", age_days=4.0, citation_count=250)
    monkeypatch.setattr(digest_mod, "papers_arrived_since", lambda *a, **k: [fresh, cited])
    boosts = _boosts({"arxiv:2401.00001": 0.0, "arxiv:2401.00002": math.log1p(250.0)})
    monkeypatch.setattr(digest_mod, "breakthrough_many", lambda *a, **k: boosts)

    ranked = rank_window(None, days=7, k=2)
    assert [r.paper.id for r in ranked] == ["arxiv:2401.00002", "arxiv:2401.00001"]
    assert ranked[0].citations == 250.0
    assert ranked[0].contributions == {"citation": math.log1p(250.0)}
    assert ranked[1].contributions == {}


def test_build_digest_post_checks_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    papers = [_paper("arxiv:2401.00001", age_days=1)]
    monkeypatch.setattr(digest_mod, "papers_arrived_since", lambda *a, **k: papers)
    monkeypatch.setattr(
        digest_mod, "breakthrough_many", lambda *a, **k: _boosts({papers[0].id: 0.0})
    )
    llm = FakeLLM("Big week [arxiv:2401.00001], also fake [arxiv:9999.99999].")

    digest = build_digest(None, llm, days=7, k=5)

    assert digest is not None
    assert digest.cited == ["arxiv:2401.00001"]  # the invented id is dropped
    assert digest.slug == week_slug(datetime.now(UTC))
    assert digest.kind == "weekly"
    assert digest.summary == "The week's top 1 papers, ranked."
    assert "Paper arxiv:2401.00001" in llm.last_user


def test_build_digest_empty_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(digest_mod, "papers_arrived_since", lambda *a, **k: [])
    monkeypatch.setattr(digest_mod, "breakthrough_many", lambda *a, **k: {})
    assert build_digest(None, FakeLLM("unused"), days=7) is None


def test_title_uses_the_iso_year() -> None:
    # Jan 1 2027 sits in ISO week 2026-w53; slug and title must agree on the year.
    end = datetime(2027, 1, 1, tzinfo=UTC)
    items = [
        digest_mod.RankedPaper(
            paper=_paper("arxiv:2401.00001", age_days=1), score=1.0, citations=0.0
        )
    ]

    digest = compose_digest(
        FakeLLM("Quiet week [arxiv:2401.00001]."), items, start=end - timedelta(days=7), end=end
    )

    assert digest.slug == "2026-w53"
    assert digest.title == "Research radar, week 53 2026"


class _DeadLLM(LLM):
    model = "fake"

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        raise RuntimeError("Error code: 429 - Rate limit exceeded: free-models-per-day")


def test_build_digest_survives_a_dead_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    papers = [_paper("arxiv:2401.00001", age_days=1)]
    monkeypatch.setattr(digest_mod, "papers_arrived_since", lambda *a, **k: papers)
    monkeypatch.setattr(
        digest_mod, "breakthrough_many", lambda *a, **k: _boosts({papers[0].id: 0.0})
    )

    digest = build_digest(None, _DeadLLM(), days=7, k=5)

    assert digest is not None
    assert digest.llm_ok is False
    assert digest.body.startswith("The digest model was unavailable")
    assert "1. [arxiv:2401.00001] Paper arxiv:2401.00001" in digest.body
    assert digest.cited == ["arxiv:2401.00001"]
