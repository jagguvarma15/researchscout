"""Parsing Epoch AI's two CSVs.

Against text rather than the network, so a change of shape upstream shows up here as a failing
parse rather than as a page that has quietly gone empty. The awkward cells are real ones taken
from the published files: a Link column holding two URLs on separate lines, compute in
scientific notation, a Domain that is several domains, and blanks everywhere.
"""

from datetime import date
from pathlib import Path

from researchscout.sources.epoch import (
    arxiv_id_in,
    benchmark_name,
    parse_benchmarks,
    parse_models,
    score_column,
)

FIXTURES = Path(__file__).parent / "fixtures"
MODELS_CSV = (FIXTURES / "epoch_models.csv").read_text()
BENCHMARKS_ZIP = (FIXTURES / "epoch_benchmarks.zip").read_bytes()


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


def test_every_benchmark_in_the_archive_is_parsed() -> None:
    """One CSV per benchmark, so the archive is the unit rather than a row."""
    scores = parse_benchmarks(BENCHMARKS_ZIP)
    by_benchmark = {s.benchmark for s in scores}

    assert by_benchmark == {"GPQA Diamond", "Terminal-Bench", "Vending bench 2"}
    # The capabilities index is a composite of the others rather than a test anyone sat, so it
    # would appear on a leaderboard as a score with nothing behind it.
    assert "Epoch capabilities index" not in by_benchmark


def test_the_score_column_is_found_by_position_not_by_name() -> None:
    """Seventy files, seventy names for the score. What they share is a shape."""
    rows = [{"Model version": "m", "Agent": "Terminus", "Accuracy mean": "0.8"}]
    assert score_column(["Model version", "Agent", "Accuracy mean"], rows) == "Accuracy mean"

    # Nothing numeric before the metadata block: better to skip the file than to invent a score.
    assert score_column(["Model version", "Harness", "Release date"], [{"Harness": "a"}]) is None
    # And nothing after that block counts, however numeric it looks.
    numeric = [
        {"Model version": "m", "Release date": "2026-01-01", "Training compute (FLOP)": "1e25"}
    ]
    assert (
        score_column(["Model version", "Release date", "Training compute (FLOP)"], numeric) is None
    )


def test_a_categorical_column_does_not_become_the_score() -> None:
    scores = {
        s.model: s for s in parse_benchmarks(BENCHMARKS_ZIP) if s.benchmark == "Terminal-Bench"
    }
    assert scores["Claude Opus 5"].score == 0.847
    assert scores["Qwen3 Max"].score == 0.612


def test_scores_that_are_not_fractions_survive_unscaled() -> None:
    """Eleven of the hub's benchmarks are a ratio, an Elo or an amount of money."""
    vending = {
        s.model: s.score
        for s in parse_benchmarks(BENCHMARKS_ZIP)
        if s.benchmark == "Vending bench 2"
    }
    assert vending["Claude Opus 5"] == 11181.87
    assert vending["Qwen3 Max"] == -31.18


def test_the_benchmark_carries_the_organisation_that_built_the_model() -> None:
    """Most measured models are not in the notable catalogue; this is what gives them a row."""
    gpqa = {s.model: s for s in parse_benchmarks(BENCHMARKS_ZIP) if s.benchmark == "GPQA Diamond"}
    assert gpqa["Claude Opus 5"].organization == "Anthropic"
    assert gpqa["Claude Opus 5"].training_compute_flop == 2.1e26
    assert gpqa["Gemini 3.6 Flash"].training_compute_flop is None


def test_a_row_without_a_score_is_skipped() -> None:
    assert "Broken" not in {s.model for s in parse_benchmarks(BENCHMARKS_ZIP)}


def test_where_a_score_came_from_is_recorded() -> None:
    scores = {s.benchmark: s.origin for s in parse_benchmarks(BENCHMARKS_ZIP)}
    assert scores["GPQA Diamond"] == "Epoch AI"
    assert scores["Terminal-Bench"] == "External leaderboard"


def test_the_file_name_is_the_benchmark_name() -> None:
    # The rows inside carry models, not the thing they were measured against.
    assert benchmark_name("gpqa_diamond.csv") == "GPQA Diamond"
    assert benchmark_name("swe_bench_verified.csv") == "SWE-bench Verified"
    # The external marker is provenance, recorded per score, not part of the name.
    assert benchmark_name("terminalbench_external.csv") == "Terminal-Bench"
    # Anything unlisted still gets a serviceable name rather than nothing.
    assert benchmark_name("some_new_bench_external.csv") == "Some new bench"


def test_an_empty_input_parses_to_nothing() -> None:
    assert parse_models("") == []
