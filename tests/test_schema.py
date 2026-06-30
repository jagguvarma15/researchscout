from datetime import UTC, datetime

import pytest

from researchscout.schema import (
    Author,
    Paper,
    Signal,
    SignalType,
    canonical_id,
    normalize_arxiv_id,
)


def _dt() -> datetime:
    return datetime(2026, 1, 15, tzinfo=UTC)


def test_paper_validates_and_defaults() -> None:
    p = Paper(
        id="arxiv:2401.12345",
        external_ids={"arxiv": "2401.12345"},
        title="A Title",
        abstract="An abstract.",
        published_at=_dt(),
        source="arxiv",
    )
    assert p.authors == []
    assert p.categories == []
    assert p.updated_at is None


def test_signal_validates() -> None:
    s = Signal(
        paper_id="arxiv:2401.12345",
        type=SignalType.citation,
        source="semantic_scholar",
        value=12.0,
        observed_at=_dt(),
    )
    assert s.type is SignalType.citation
    assert s.metadata == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2401.12345", "2401.12345"),
        ("2401.12345v2", "2401.12345"),
        ("arXiv:2401.12345v10", "2401.12345"),
        ("  2401.12345v1  ", "2401.12345"),
    ],
)
def test_normalize_arxiv_id(raw: str, expected: str) -> None:
    assert normalize_arxiv_id(raw) == expected


def test_canonical_id_prefers_arxiv() -> None:
    cid = canonical_id({"arxiv": "2401.12345v2", "doi": "10.1/x"}, "Some Title", [])
    assert cid == "arxiv:2401.12345"


def test_canonical_id_falls_back_to_doi() -> None:
    cid = canonical_id({"doi": "10.1000/XYZ"}, "Some Title", [])
    assert cid == "doi:10.1000/xyz"


def test_canonical_id_hashes_title_and_author() -> None:
    cid = canonical_id({}, "Attention Is All You Need", [Author(name="Ashish Vaswani")])
    assert cid.startswith("hash:")


def test_canonical_id_stable_across_descriptions() -> None:
    a = canonical_id({}, "Attention Is All You Need!", [Author(name="A. Vaswani")])
    b = canonical_id({}, "attention   is all  you need", [Author(name="Someone Vaswani")])
    assert a == b


def test_canonical_id_is_version_independent() -> None:
    v1 = canonical_id({"arxiv": "2401.12345v1"}, "x", [])
    v3 = canonical_id({"arxiv": "2401.12345v3"}, "x", [])
    assert v1 == v3 == "arxiv:2401.12345"
