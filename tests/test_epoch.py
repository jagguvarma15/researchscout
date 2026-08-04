"""Parsing Epoch AI's two CSVs.

Against text rather than the network, so a change of shape upstream shows up here as a failing
parse rather than as a page that has quietly gone empty. The awkward cells are real ones taken
from the published files: a Link column holding two URLs on separate lines, compute in
scientific notation, a Domain that is several domains, and blanks everywhere.
"""

from datetime import date
from pathlib import Path

from researchscout.sources.epoch import arxiv_id_in, parse_benchmarks, parse_models

FIXTURES = Path(__file__).parent / "fixtures"
MODELS_CSV = (FIXTURES / "epoch_models.csv").read_text()
BENCHMARKS_CSV = (FIXTURES / "epoch_benchmarks.csv").read_text()


def test_models_parse_the_ordinary_row() -> None:
    kimi = parse_models(MODELS_CSV)[0]
    assert kimi.name == "Kimi K3"
    assert kimi.organization == "Moonshot AI"
    assert kimi.publication_date == date(2026, 7, 16)
    assert kimi.domains == ["Language", "Multimodal", "Vision"]
    assert kimi.parameters == 2.8e12
    assert kimi.training_compute_flop == 2.0001e25
    assert kimi.accessibility == "Open weights (non-commercial)"
    assert kimi.open_weights is True
    assert kimi.arxiv_id == "2605.02881"


def test_blank_cells_become_none_not_empty_strings() -> None:
    spar3d = parse_models(MODELS_CSV)[1]
    assert spar3d.parameters is None
    assert spar3d.training_compute_flop is None


def test_a_row_without_a_model_name_is_skipped() -> None:
    assert [record.name for record in parse_models(MODELS_CSV)] == [
        "Kimi K3",
        "Stable Point Aware 3D",
        "Closed Thing",
        "Bad Numbers",
    ]


def test_unparseable_numbers_and_dates_cost_their_column_not_the_row() -> None:
    # One malformed parameter count should not take a model off the page.
    bad = parse_models(MODELS_CSV)[3]
    assert bad.name == "Bad Numbers"
    assert bad.parameters is None
    assert bad.training_compute_flop is None
    assert bad.publication_date is None


def test_a_link_that_is_not_arxiv_yields_no_id() -> None:
    closed = parse_models(MODELS_CSV)[2]
    assert closed.link == "https://example.com/blog"
    assert closed.arxiv_id is None
    assert closed.open_weights is False


def test_arxiv_id_is_found_among_several_links() -> None:
    # The column really does hold a paper and a technical report on two lines.
    both = "https://arxiv.org/abs/2512.20856\nhttps://research.nvidia.com/report.pdf "
    assert arxiv_id_in(both) == "2512.20856"
    assert arxiv_id_in("https://arxiv.org/pdf/2501.04689v3") == "2501.04689"
    assert arxiv_id_in(None) is None
    assert arxiv_id_in("https://openai.com/index/gpt-4") is None


def test_benchmarks_parse_scores() -> None:
    scores = parse_benchmarks(BENCHMARKS_CSV)
    assert [(s.benchmark, s.model, s.score) for s in scores] == [
        ("Lech Mazur Writing", "Amazon Nova Pro", 0.605),
        ("MMLU", "Amazon Nova Pro", 0.76),
    ]
    assert scores[1].benchmark_release_date == date(2020, 9, 7)
    assert scores[1].measured_on == date(2024, 12, 3)
    assert scores[1].origin == "Stanford CRFM Leaderboard"


def test_a_score_without_a_number_or_a_benchmark_is_skipped() -> None:
    # Both would produce a leaderboard row that says nothing.
    names = {s.model for s in parse_benchmarks(BENCHMARKS_CSV)}
    assert "Broken" not in names
    assert "No Benchmark" not in names


def test_an_empty_file_parses_to_nothing() -> None:
    assert parse_models("") == []
    assert parse_benchmarks("") == []
