"""The catalogue store: merging two upstreams, and the join to the corpus.

Integration, because the behaviour worth pinning is what the upsert does -- that a second
source fills gaps without overwriting what the first knew, that a refresh converges instead of
accumulating, and that a benchmark score is kept whether or not its model is in the catalogue.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from researchscout.schema import Author, Paper
from researchscout.store import catalog
from researchscout.store.catalog import ModelUpsert
from researchscout.store.papers import upsert_paper

pytestmark = pytest.mark.integration


def _paper(session: Session, pid: str, arxiv: str) -> None:
    upsert_paper(
        session,
        Paper(
            id=pid,
            external_ids={"arxiv": arxiv},
            title="Robust Speech Recognition",
            abstract="An abstract.",
            authors=[Author(name="Jane Doe")],
            categories=["cs.CL"],
            primary_category="cs.CL",
            published_at=datetime(2022, 12, 6, tzinfo=UTC),
            source="arxiv",
        ),
    )
    session.flush()


def test_slug_makes_one_key_of_two_spellings() -> None:
    assert catalog.slug("GPT-4o") == catalog.slug("GPT 4o") == "gpt-4o"
    assert catalog.slug("LLaMA-3") == catalog.slug("Llama 3") == "llama-3"
    assert catalog.slug("Café Model") == "cafe-model"
    assert catalog.slug("!!!") == ""


def test_a_model_round_trips(session: Session) -> None:
    catalog.upsert_models(
        session,
        [
            ModelUpsert(
                name="Whisper",
                organization="OpenAI",
                publication_date=date(2022, 12, 6),
                domains="Speech",
                parameters=1.55e9,
                open_weights=True,
                source="epoch_ai",
            )
        ],
    )
    session.flush()
    stored = catalog.get_model(session, "whisper")
    assert stored is not None
    assert stored.name == "Whisper"
    assert stored.organization == "OpenAI"
    assert stored.parameters == 1.55e9
    assert stored.open_weights is True


def test_a_second_source_fills_gaps_without_overwriting(session: Session) -> None:
    """Epoch knows the compute, the Hub knows the downloads, and the row ends up with both."""
    catalog.upsert_models(
        session,
        [
            ModelUpsert(
                name="Whisper",
                organization="OpenAI",
                training_compute_flop=1.0e22,
                source="epoch_ai",
            )
        ],
    )
    catalog.upsert_models(
        session,
        [
            ModelUpsert(
                name="whisper",  # the Hub spells it differently; the slug is the same
                hf_repo="openai/whisper-large-v3",
                hf_downloads=4_000_000,
                source="huggingface_models",
            )
        ],
    )
    session.flush()
    stored = catalog.get_model(session, "whisper")
    assert stored is not None
    assert stored.training_compute_flop == 1.0e22  # not clobbered by the second write
    assert stored.organization == "OpenAI"  # nor this
    assert stored.hf_downloads == 4_000_000  # and the gap is filled
    assert catalog.count_models(session) == 1


def test_a_repeated_refresh_converges(session: Session) -> None:
    models = [ModelUpsert(name="Whisper", organization="OpenAI", source="epoch_ai")]
    catalog.upsert_models(session, models)
    catalog.upsert_models(session, models)
    session.flush()
    assert catalog.count_models(session) == 1


def test_a_model_with_no_usable_name_is_skipped(session: Session) -> None:
    assert catalog.upsert_models(session, [ModelUpsert(name="!!!", source="epoch_ai")]) == 0
    assert catalog.count_models(session) == 0


def test_linking_reaches_the_paper_and_back(session: Session) -> None:
    """The join the whole feature turns on."""
    _paper(session, "arxiv:2212.04356", "2212.04356")
    catalog.upsert_models(session, [ModelUpsert(name="Whisper", source="epoch_ai")])
    session.flush()

    assert catalog.link_models_to_papers(session, {"whisper": "arxiv:2212.04356"}) == 1
    session.flush()
    assert [m.name for m in catalog.models_for_paper(session, "arxiv:2212.04356")] == ["Whisper"]
    assert catalog.count_models(session, with_paper=True) == 1


def test_linking_to_a_paper_this_corpus_lacks_is_skipped(session: Session) -> None:
    # Most models in the world have no paper here; that is not an error.
    catalog.upsert_models(session, [ModelUpsert(name="Whisper", source="epoch_ai")])
    session.flush()
    assert catalog.link_models_to_papers(session, {"whisper": "arxiv:9999.99999"}) == 0
    assert catalog.count_models(session, with_paper=True) == 0


def test_model_filters(session: Session) -> None:
    catalog.upsert_models(
        session,
        [
            ModelUpsert(
                name="Whisper",
                organization="OpenAI",
                domains="Speech",
                open_weights=True,
                publication_date=date(2022, 12, 6),
                source="epoch_ai",
            ),
            ModelUpsert(
                name="Claude Opus 5",
                organization="Anthropic",
                domains="Language",
                open_weights=False,
                publication_date=date(2026, 1, 1),
                source="epoch_ai",
            ),
        ],
    )
    session.flush()
    assert [m.name for m in catalog.list_models(session)] == ["Claude Opus 5", "Whisper"]
    assert [m.name for m in catalog.list_models(session, organization="anthropic")] == [
        "Claude Opus 5"
    ]
    assert [m.name for m in catalog.list_models(session, domain="Speech")] == ["Whisper"]
    assert [m.name for m in catalog.list_models(session, open_weights=True)] == ["Whisper"]
    assert catalog.count_models(session, open_weights=False) == 1


def test_a_benchmark_keeps_scores_whether_or_not_the_model_is_known(session: Session) -> None:
    """Half the benchmarked models are not in the catalogue; their scores still count."""
    catalog.upsert_models(session, [ModelUpsert(name="Claude Opus 5", source="epoch_ai")])
    session.flush()
    known = catalog.known_model_ids(session)

    written = catalog.replace_benchmark_results(
        session,
        "GPQA diamond",
        date(2023, 11, 20),
        [
            ("Claude Opus 5", 0.918, date(2026, 1, 1), "Epoch"),
            ("Gemini 3.6 Flash", 0.922, date(2026, 2, 1), "Epoch"),
        ],
        known,
    )
    session.flush()
    assert written == 2

    board = catalog.leaderboard(session, "gpqa-diamond")
    assert [(r.model_name, r.model_id) for r in board] == [
        ("Gemini 3.6 Flash", None),  # best score, not in the catalogue, still listed
        ("Claude Opus 5", "claude-opus-5"),
    ]
    assert [b.name for b in catalog.list_benchmarks(session)] == ["GPQA diamond"]
    assert catalog.results_for_model(session, "claude-opus-5") == [("GPQA diamond", 0.918)]


def test_rescoring_a_benchmark_updates_rather_than_duplicates(session: Session) -> None:
    for score in (0.5, 0.9):
        catalog.replace_benchmark_results(
            session, "MMLU", None, [("Some Model", score, None, "Epoch")], set()
        )
    session.flush()
    board = catalog.leaderboard(session, "mmlu")
    assert [(r.model_name, r.score) for r in board] == [("Some Model", 0.9)]


def test_counts_report_what_is_stored(session: Session) -> None:
    _paper(session, "arxiv:2212.04356", "2212.04356")
    catalog.upsert_models(session, [ModelUpsert(name="Whisper", source="epoch_ai")])
    session.flush()
    catalog.link_models_to_papers(session, {"whisper": "arxiv:2212.04356"})
    catalog.replace_benchmark_results(
        session, "MMLU", None, [("Whisper", 0.4, None, None)], {"whisper"}
    )
    session.flush()
    assert catalog.catalog_counts(session) == {
        "models": 1,
        "benchmarks": 1,
        "results": 1,
        "linked": 1,
    }
