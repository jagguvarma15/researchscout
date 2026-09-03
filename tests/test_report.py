from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import researchscout.report as report_mod
from researchscout.report import build_daily_report, day_slug
from researchscout.schema import Author, Paper


class _Score(SimpleNamespace):
    pass


def _paper(pid: str, title: str, *, category: str = "cs.LG", hours_old: int = 3) -> Paper:
    return Paper(
        id=pid,
        title=title,
        abstract="A",
        authors=[Author(name="X")],
        categories=[category],
        primary_category=category,
        published_at=datetime.now(UTC) - timedelta(hours=hours_old),
        source="arxiv",
        citation_count=2,
    )


def test_day_slug_format() -> None:
    assert day_slug(datetime(2026, 7, 30, 6, tzinfo=UTC)) == "2026-07-30"


def test_empty_window_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_mod, "papers_arrived_since", lambda *a, **k: [])
    assert build_daily_report(None) is None


def test_report_ranks_counts_and_cites(monkeypatch: pytest.MonkeyPatch) -> None:
    papers = [
        _paper("arxiv:2607.1", "Quiet paper"),
        _paper("arxiv:2607.2", "Hot paper"),
        _paper("arxiv:2607.3", "Physics paper", category="hep-th"),
    ]
    scores = {
        "arxiv:2607.1": _Score(total=0.1, contributions={}),
        "arxiv:2607.2": _Score(total=3.0, contributions={"citation": 2.5, "discussion": 0.5}),
        "arxiv:2607.3": _Score(total=1.0, contributions={"citation": 1.0}),
    }
    topics = [
        SimpleNamespace(label="Efficient attention", trend="rising", size=6),
        SimpleNamespace(label="Old stable topic", trend="steady", size=9),
    ]
    monkeypatch.setattr(report_mod, "papers_arrived_since", lambda *a, **k: papers)
    monkeypatch.setattr(report_mod, "breakthrough_many", lambda session, ids: scores)
    monkeypatch.setattr(report_mod, "list_topics", lambda session: topics)

    now = datetime(2026, 7, 30, 6, tzinfo=UTC)
    digest = build_daily_report(None, now=now)

    assert digest is not None
    assert digest.slug == "2026-07-30"
    assert digest.title == "Daily report 2026-07-30"
    assert [item.paper.id for item in digest.items] == [
        "arxiv:2607.2",
        "arxiv:2607.3",
        "arxiv:2607.1",
    ]
    assert digest.cited == ["arxiv:2607.2", "arxiv:2607.3", "arxiv:2607.1"]
    assert "3 papers arrived in the last 24 hours." in digest.body
    assert "Computer Science 2" in digest.body and "Physics 1" in digest.body
    assert "- Efficient attention: rising (size 6)" in digest.body
    assert "Old stable topic" not in digest.body  # steady topics are not movements
    assert "1. [arxiv:2607.2] Hot paper" in digest.body
    assert digest.period_end == now
    assert digest.kind == "daily"
    assert digest.llm_ok is True  # no prose call happened, so no fallback either
    assert digest.summary == "3 arrivals; 3 must-reads."
    assert digest.items[0].contributions == {"citation": 2.5, "discussion": 0.5}


def test_ties_break_by_recency(monkeypatch: pytest.MonkeyPatch) -> None:
    papers = [
        _paper("arxiv:2607.4", "Older", hours_old=20),
        _paper("arxiv:2607.5", "Newer", hours_old=1),
    ]
    scores = {pid: _Score(total=0.0, contributions={}) for pid in ("arxiv:2607.4", "arxiv:2607.5")}
    monkeypatch.setattr(report_mod, "papers_arrived_since", lambda *a, **k: papers)
    monkeypatch.setattr(report_mod, "breakthrough_many", lambda session, ids: scores)
    monkeypatch.setattr(report_mod, "list_topics", lambda session: [])

    digest = build_daily_report(None)
    assert digest is not None
    assert [item.paper.id for item in digest.items] == ["arxiv:2607.5", "arxiv:2607.4"]


def test_day_slug_follows_the_scheduler_zone() -> None:
    """An evening-ET run is already tomorrow in UTC; the slug must stay on the ET date."""
    from zoneinfo import ZoneInfo

    evening_et_in_utc = datetime(2026, 8, 18, 1, 0, tzinfo=UTC)  # Aug 17, 21:00 in New York
    assert day_slug(evening_et_in_utc) == "2026-08-18"  # UTC default keeps old behavior
    assert day_slug(evening_et_in_utc, ZoneInfo("America/New_York")) == "2026-08-17"
