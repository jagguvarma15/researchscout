"""The reading-list export builders: pure text in, reference-manager-ready text out."""

from datetime import UTC, datetime

from researchscout.export import bibtex_entry, bibtex_export, csv_export
from researchscout.schema import Author, Paper
from researchscout.store.saved import SavedEntry


def _paper() -> Paper:
    return Paper(
        id="arxiv:2401.00001",
        external_ids={"arxiv": "2401.00001"},
        title="Sparse Attention at Scale",
        abstract="A",
        authors=[Author(name="Ada Lovelace"), Author(name="Alan Turing")],
        categories=["cs.LG"],
        published_at=datetime(2024, 1, 15, tzinfo=UTC),
        source="arxiv",
        venue="NeurIPS",
        url="https://arxiv.org/abs/2401.00001",
    )


def _entry(note: str | None = "worth rereading") -> SavedEntry:
    return SavedEntry(
        paper=_paper(),
        status="done",
        tags=["attention", "efficiency"],
        note=note,
        saved_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def test_bibtex_entry_carries_the_shared_fields() -> None:
    entry = bibtex_entry(_paper())
    assert entry.startswith("@article{arxiv-2401-00001,")
    assert "  title = {Sparse Attention at Scale}," in entry
    assert "  author = {Ada Lovelace and Alan Turing}," in entry
    assert "  year = {2024}," in entry
    assert "  eprint = {2401.00001}," in entry
    assert "  journal = {NeurIPS}," in entry
    assert entry.endswith("}")


def test_bibtex_export_joins_and_terminates() -> None:
    text = bibtex_export([_entry(), _entry()])
    assert text.count("@article{") == 2
    assert text.endswith("}\n")
    assert bibtex_export([]) == ""


def test_csv_export_includes_library_fields() -> None:
    text = csv_export([_entry()])
    lines = text.splitlines()
    assert lines[0] == "id,title,authors,published,venue,status,tags,note,url"
    assert "arxiv:2401.00001" in lines[1]
    assert "Ada Lovelace; Alan Turing" in lines[1]
    assert "attention; efficiency" in lines[1]
    assert "worth rereading" in lines[1]


def test_csv_export_blanks_missing_fields() -> None:
    text = csv_export([_entry(note=None)])
    assert ",done,attention; efficiency,," in text
