from datetime import UTC, datetime
from typing import Any

import pytest

import researchscout.workers.airtable_sync as sync_mod
from researchscout.events.schemas import DigestPublished, PaperSaved
from researchscout.schema import Author, Paper
from researchscout.workers.airtable_sync import handle_digest, handle_saved

AT = datetime(2026, 7, 6, tzinfo=UTC)


def _paper() -> Paper:
    return Paper(
        id="arxiv:2401.00001",
        title="Cool Paper",
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source="arxiv",
        url="https://arxiv.org/abs/2401.00001",
    )


class FakeTable:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.updated: list[tuple[str, Any]] = []

    def all(self, formula: object = None) -> list[dict[str, Any]]:
        return self.rows

    def create(self, fields: dict[str, Any]) -> None:
        self.created.append(fields)

    def delete(self, row_id: str) -> None:
        self.deleted.append(row_id)

    def update(self, row_id: str, fields: dict[str, Any]) -> None:
        self.updated.append((row_id, fields["Slug"]))


def _event(saved: bool) -> PaperSaved:
    return PaperSaved(user_sub="user-1", paper_id="arxiv:2401.00001", saved=saved, at=AT)


def test_save_creates_enriched_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync_mod, "get_paper", lambda *a: _paper())
    table = FakeTable()
    handle_saved(None, table, _event(saved=True))
    assert table.created == [
        {
            "User": "user-1",
            "Paper": "arxiv:2401.00001",
            "Title": "Cool Paper",
            "Link": "https://arxiv.org/abs/2401.00001",
            "Saved at": AT.isoformat(),
        }
    ]


def test_save_replay_does_not_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync_mod, "get_paper", lambda *a: _paper())
    table = FakeTable(rows=[{"id": "rec1", "fields": {}}])
    handle_saved(None, table, _event(saved=True))
    assert table.created == []


def test_unsave_deletes_rows() -> None:
    table = FakeTable(rows=[{"id": "rec1", "fields": {}}, {"id": "rec2", "fields": {}}])
    handle_saved(None, table, _event(saved=False))
    assert table.deleted == ["rec1", "rec2"]
    assert table.created == []


def test_unsave_replay_is_silent() -> None:
    table = FakeTable()
    handle_saved(None, table, _event(saved=False))
    assert table.deleted == []


def test_save_survives_missing_paper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync_mod, "get_paper", lambda *a: None)
    table = FakeTable()
    handle_saved(None, table, _event(saved=True))
    assert table.created[0]["Title"] == "arxiv:2401.00001"
    assert table.created[0]["Link"] == ""


def _digest_event() -> DigestPublished:
    return DigestPublished(
        slug="2026-w28",
        title="Research radar, week 28 2026",
        period_start=datetime(2026, 6, 29, tzinfo=UTC),
        period_end=AT,
    )


def test_digest_archives_once() -> None:
    table = FakeTable()
    handle_digest(table, _digest_event())
    assert table.created[0]["Slug"] == "2026-w28"
    assert table.created[0]["Title"].startswith("Research radar")


def test_digest_republish_updates_in_place() -> None:
    table = FakeTable(rows=[{"id": "rec1", "fields": {"Slug": "2026-w28"}}])
    handle_digest(table, _digest_event())
    assert table.created == []
    assert table.updated == [("rec1", "2026-w28")]
