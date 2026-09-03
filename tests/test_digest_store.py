from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from researchscout.digest import Digest, RankedPaper
from researchscout.schema import Author, Paper
from researchscout.store.digests import count_digests, get_digest, list_digests, upsert_digest

pytestmark = pytest.mark.integration

END = datetime(2026, 7, 6, tzinfo=UTC)


def _paper(enriched: bool = False) -> Paper:
    return Paper(
        id="arxiv:2401.00001",
        title="T",
        abstract="A",
        authors=[Author(name="X"), Author(name="Y"), Author(name="Z"), Author(name="W")],
        categories=["cs.LG"],
        primary_category="cs.LG" if enriched else None,
        venue="NeurIPS 2026" if enriched else None,
        published_at=END - timedelta(days=2),
        source="arxiv",
        keywords=["attention", "efficiency", "kv cache", "latency", "fifth"] if enriched else None,
    )


def _digest(slug: str = "2026-w28", body: str = "Big week.", *, enriched: bool = False) -> Digest:
    return Digest(
        slug=slug,
        title="Research radar, week 28 2026",
        period_start=END - timedelta(days=7),
        period_end=END,
        body=body,
        cited=["arxiv:2401.00001"],
        items=[
            RankedPaper(
                paper=_paper(enriched),
                score=0.9,
                citations=12.0,
                contributions={"citation": 1.2345} if enriched else {},
            )
        ],
    )


def test_upsert_get_roundtrip(session: Session) -> None:
    upsert_digest(session, _digest())
    row = get_digest(session, "2026-w28")
    assert row is not None
    assert row.body == "Big week."
    assert row.kind == "weekly"
    assert row.llm_ok is True
    assert row.items[0]["paper_id"] == "arxiv:2401.00001"
    assert row.items[0]["citations"] == 12.0


def test_enriched_items_and_empty_key_omission(session: Session) -> None:
    upsert_digest(session, _digest(enriched=True))
    enriched = get_digest(session, "2026-w28")
    assert enriched is not None
    item = enriched.items[0]
    assert item["primary_category"] == "cs.LG"
    assert item["keywords"] == ["attention", "efficiency", "kv cache", "latency"]  # capped at 4
    assert item["authors"] == ["X", "Y", "Z"]  # capped at 3
    assert item["author_count"] == 4
    assert item["venue"] == "NeurIPS 2026"
    assert item["why"] == {"citation": 1.234}  # rounded

    upsert_digest(session, _digest(slug="2026-w27"))
    bare = get_digest(session, "2026-w27")
    assert bare is not None
    # Empty enrichment is omitted, never stored as JSON null.
    assert set(bare.items[0]) == {
        "paper_id",
        "title",
        "score",
        "citations",
        "authors",
        "author_count",
    }


def test_fallback_flag_round_trips(session: Session) -> None:
    fallback = _digest(body="The digest model was unavailable this week")
    fallback.llm_ok = False
    upsert_digest(session, fallback)
    row = get_digest(session, "2026-w28")
    assert row is not None
    assert row.llm_ok is False


def test_rerun_replaces_the_week(session: Session) -> None:
    upsert_digest(session, _digest(body="First take."))
    upsert_digest(session, _digest(body="Second take."))
    row = get_digest(session, "2026-w28")
    assert row is not None
    assert row.body == "Second take."
    assert len(list_digests(session)) == 1


def test_list_is_newest_first(session: Session) -> None:
    older = _digest(slug="2026-w27")
    older.period_end = END - timedelta(days=7)
    upsert_digest(session, older)
    upsert_digest(session, _digest(slug="2026-w28"))
    assert [row.slug for row in list_digests(session)] == ["2026-w28", "2026-w27"]


def test_kind_filter_offset_and_count(session: Session) -> None:
    daily = _digest(slug="2026-07-05")
    daily.kind = "daily"
    daily.period_end = END - timedelta(days=1)
    upsert_digest(session, daily)
    older = _digest(slug="2026-w27")
    older.period_end = END - timedelta(days=7)
    upsert_digest(session, older)
    upsert_digest(session, _digest(slug="2026-w28"))

    assert [row.slug for row in list_digests(session, kind="weekly")] == ["2026-w28", "2026-w27"]
    assert [row.slug for row in list_digests(session, kind="daily")] == ["2026-07-05"]
    assert [row.slug for row in list_digests(session, limit=1, offset=1)] == ["2026-07-05"]
    assert count_digests(session) == 3
    assert count_digests(session, kind="weekly") == 2
    assert count_digests(session, kind="daily") == 1


def test_legacy_row_still_lists(session: Session) -> None:
    # A pre-0035 row: raw insert with only the old columns; kind/llm_ok take server defaults.
    session.execute(
        text(
            "INSERT INTO digests (slug, title, period_start, period_end, body, items) "
            "VALUES ('2026-w20', 'Old issue', :start, :end, 'Old body', "
            '\'[{"paper_id": "arxiv:2401.00001", "title": "T", '
            '"score": 0.5, "citations": 3.0}]\'::jsonb)'
        ),
        {"start": END - timedelta(days=60), "end": END - timedelta(days=53)},
    )
    rows = list_digests(session, kind="weekly")
    assert [row.slug for row in rows] == ["2026-w20"]
    assert rows[0].kind == "weekly"
    assert rows[0].llm_ok is True
    assert set(rows[0].items[0]) == {"paper_id", "title", "score", "citations"}
