"""The catalogue store: merging two upstreams, and the join to the corpus.

Integration, because the behaviour worth pinning is what the upsert does -- that a second
source fills gaps without overwriting what the first knew, that a refresh converges instead of
accumulating, and that a benchmark score is kept whether or not its model is in the catalogue.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from researchscout.catalog import EPOCH_FIELDS, HF_FIELDS
from researchscout.providers import parse_providers
from researchscout.schema import Author, Paper
from researchscout.store import catalog
from researchscout.store.catalog import ModelFilters, ModelUpsert
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
    assert catalog.count_models(session, filters=ModelFilters(with_paper=True)) == 1


def test_linking_to_a_paper_this_corpus_lacks_is_skipped(session: Session) -> None:
    # Most models in the world have no paper here; that is not an error.
    catalog.upsert_models(session, [ModelUpsert(name="Whisper", source="epoch_ai")])
    session.flush()
    assert catalog.link_models_to_papers(session, {"whisper": "arxiv:9999.99999"}) == 0
    assert catalog.count_models(session, filters=ModelFilters(with_paper=True)) == 0


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
    assert [
        m.name for m in catalog.list_models(session, filters=ModelFilters(organization="anthropic"))
    ] == ["Claude Opus 5"]
    assert [
        m.name for m in catalog.list_models(session, filters=ModelFilters(domain="Speech"))
    ] == ["Whisper"]
    assert [
        m.name for m in catalog.list_models(session, filters=ModelFilters(open_weights=True))
    ] == ["Whisper"]
    assert catalog.count_models(session, filters=ModelFilters(open_weights=False)) == 1


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
    assert catalog.results_for_model(session, "claude-opus-5") == [
        ("GPQA diamond", 0.918, "fraction")
    ]


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


def test_the_hub_cannot_overwrite_what_epoch_ai_is_authoritative_for(session: Session) -> None:
    """The bug this fixes: running second was the same as being right, and the Hub runs second.

    Every field here is one both upstreams supply, so before per-field authority the Hub's
    version simply won: a repository URL where the paper link should be, a repository owner
    where the lab should be, and - worst - open weights on a closed model whose name happened
    to slug the same as somebody's upload.
    """
    catalog.upsert_models(
        session,
        [
            ModelUpsert(
                name="Qwen3 Max",
                organization="Alibaba",
                task="Chat",
                link="https://arxiv.org/abs/2605.02881",
                open_weights=False,
                accessibility="API access",
                source="epoch_ai",
            )
        ],
        authoritative=EPOCH_FIELDS,
    )
    catalog.upsert_models(
        session,
        [
            ModelUpsert(
                name="Qwen3-Max",  # same slug
                organization="Qwen",
                task="text-generation",
                link="https://huggingface.co/Qwen/Qwen3-Max",
                open_weights=True,
                hf_repo="Qwen/Qwen3-Max",
                hf_downloads=1_200_000,
                source="huggingface_models",
            )
        ],
        authoritative=HF_FIELDS,
    )
    session.flush()

    stored = catalog.get_model(session, "qwen3-max")
    assert stored is not None
    assert stored.organization == "Alibaba"
    assert stored.task == "Chat"
    assert stored.link == "https://arxiv.org/abs/2605.02881"
    assert stored.open_weights is False
    assert stored.accessibility == "API access"
    # The half the Hub is the authority for still lands.
    assert stored.hf_repo == "Qwen/Qwen3-Max"
    assert stored.hf_downloads == 1_200_000


def test_the_order_the_sources_run_in_no_longer_decides(session: Session) -> None:
    """The same two writes the other way round leave the same row."""
    hub = [
        ModelUpsert(
            name="Qwen3-Max",
            organization="Qwen",
            link="https://huggingface.co/Qwen/Qwen3-Max",
            open_weights=True,
            hf_downloads=7,
            source="huggingface_models",
        )
    ]
    epoch_models = [
        ModelUpsert(
            name="Qwen3 Max",
            organization="Alibaba",
            link="https://arxiv.org/abs/2605.02881",
            open_weights=False,
            source="epoch_ai",
        )
    ]
    catalog.upsert_models(session, hub, authoritative=HF_FIELDS)
    catalog.upsert_models(session, epoch_models, authoritative=EPOCH_FIELDS)
    session.flush()

    stored = catalog.get_model(session, "qwen3-max")
    assert stored is not None
    assert (stored.organization, stored.open_weights) == ("Alibaba", False)
    assert stored.link == "https://arxiv.org/abs/2605.02881"
    assert stored.hf_downloads == 7


def test_sources_accumulate_rather_than_replacing_each_other(session: Session) -> None:
    """The column exists to say a model is described by both, and could only ever say one."""
    catalog.upsert_models(session, [ModelUpsert(name="Whisper", source="epoch_ai")])
    catalog.upsert_models(session, [ModelUpsert(name="whisper", source="huggingface_models")])
    session.flush()

    stored = catalog.get_model(session, "whisper")
    assert stored is not None
    assert stored.sources == "epoch_ai,huggingface_models"

    # And a repeated refresh does not grow it.
    catalog.upsert_models(session, [ModelUpsert(name="Whisper", source="epoch_ai")])
    session.flush()
    session.refresh(stored)
    assert stored.sources == "epoch_ai,huggingface_models"


def test_models_sort_by_each_offered_key(session: Session) -> None:
    catalog.upsert_models(
        session,
        [
            ModelUpsert(
                name="Small",
                parameters=1e9,
                training_compute_flop=1e22,
                hf_downloads=10,
                publication_date=date(2024, 1, 1),
                source="epoch_ai",
            ),
            ModelUpsert(
                name="Large",
                parameters=1e12,
                training_compute_flop=1e25,
                hf_downloads=5,
                publication_date=date(2023, 1, 1),
                source="epoch_ai",
            ),
            # No numbers at all: it must sort last on every numeric key rather than first.
            ModelUpsert(name="Unknown", source="epoch_ai"),
        ],
    )
    session.flush()

    def names(sort: catalog.ModelSort) -> list[str]:
        return [m.name for m in catalog.list_models(session, sort=sort)]

    assert names("released") == ["Small", "Large", "Unknown"]
    assert names("parameters") == ["Large", "Small", "Unknown"]
    assert names("compute") == ["Large", "Small", "Unknown"]
    assert names("downloads") == ["Small", "Large", "Unknown"]
    assert names("name") == ["Large", "Small", "Unknown"]


def test_a_name_search_narrows_the_list_and_its_count(session: Session) -> None:
    catalog.upsert_models(
        session,
        [
            ModelUpsert(name="Claude Opus 5", source="epoch_ai"),
            ModelUpsert(name="Claude Haiku 4.5", source="epoch_ai"),
            ModelUpsert(name="Whisper", source="epoch_ai"),
        ],
    )
    session.flush()
    filters = ModelFilters(query="claude")
    assert len(catalog.list_models(session, filters=filters)) == 2
    assert catalog.count_models(session, filters=filters) == 2


def test_the_provider_comparison_picks_each_lab_its_newest_scored_model(
    session: Session,
) -> None:
    """Newest *scored*: a lab's latest release is often a preview nobody has evaluated."""
    catalog.upsert_models(
        session,
        [
            ModelUpsert(
                name="Claude Opus 5",
                organization="Anthropic",
                publication_date=date(2026, 1, 1),
                source="epoch_ai",
            ),
            ModelUpsert(
                name="Claude Opus 4",
                organization="Anthropic",
                publication_date=date(2025, 1, 1),
                source="epoch_ai",
            ),
            # Newer than either, and unmeasured: a row of blanks is worse than the model with
            # numbers against it, so it must not be chosen.
            ModelUpsert(
                name="Claude Preview",
                organization="Anthropic",
                publication_date=date(2026, 6, 1),
                source="epoch_ai",
            ),
            # Filed under a repository owner rather than the lab name; the aliases join them.
            ModelUpsert(
                name="Qwen3 Max",
                organization="Qwen",
                publication_date=date(2026, 2, 1),
                source="epoch_ai",
            ),
            ModelUpsert(
                name="Nobody Special",
                organization="Unlisted Lab",
                publication_date=date(2026, 5, 1),
                source="epoch_ai",
            ),
        ],
    )
    session.flush()
    known = catalog.known_model_ids(session)
    catalog.replace_benchmark_results(
        session,
        "GPQA diamond",
        None,
        [("Claude Opus 5", 0.91, None, "Epoch"), ("Qwen3 Max", 0.82, None, "Epoch")],
        known,
    )
    catalog.replace_benchmark_results(
        session, "Claude Opus 4 only", None, [("Claude Opus 4", 0.5, None, "Epoch")], known
    )
    catalog.replace_benchmark_results(
        session, "MMLU", None, [("Nobody Special", 0.99, None, "Epoch")], known
    )
    session.flush()

    config = parse_providers(
        {
            "benchmarks": ["gpqa-diamond", "mmlu"],
            "providers": [
                {"name": "Anthropic", "country": "United States"},
                {"name": "Alibaba", "country": "China", "aliases": ["Qwen"]},
                {"name": "Absent Lab", "country": "Nowhere"},
            ],
        }
    )
    entries, columns = catalog.provider_leaders(session, config)

    # Configured provider order, and only the labs that have a scored model.
    assert [entry.provider for entry in entries] == ["Anthropic", "Alibaba"]
    assert [entry.model_name for entry in entries] == ["Claude Opus 5", "Qwen3 Max"]
    assert entries[1].country == "China"
    assert entries[0].scores == {"gpqa-diamond": 0.91}

    # MMLU is configured but only an unlisted lab scored it, so the column is dropped rather
    # than drawn empty.
    assert [(c.id, c.name, c.scale) for c in columns] == [
        ("gpqa-diamond", "GPQA diamond", "fraction")
    ]


def test_the_provider_comparison_is_empty_without_configuration(session: Session) -> None:
    assert catalog.provider_leaders(session, parse_providers({})) == ([], [])


def test_a_benchmark_records_whether_its_scores_are_fractions(session: Session) -> None:
    """Eleven of the hub's benchmarks are a ratio, an Elo or an amount of money."""
    catalog.replace_benchmark_results(
        session, "GPQA Diamond", None, [("A", 0.91, None, "Epoch AI")], set()
    )
    # Dollars, and they go negative.
    catalog.replace_benchmark_results(
        session,
        "Vending Bench 2",
        None,
        [("A", 11181.87, None, "External leaderboard"), ("B", -31.18, None, "External")],
        set(),
    )
    session.flush()

    assert catalog.get_benchmark(session, "gpqa-diamond").score_scale == "fraction"
    assert catalog.get_benchmark(session, "vending-bench-2").score_scale == "raw"


def test_the_scale_is_settled_over_the_whole_set_not_the_visible_rows() -> None:
    """A leaderboard capped at fifty and a comparison showing five must agree."""
    assert catalog.score_scale([0.1, 0.9, 1.0]) == "fraction"
    assert catalog.score_scale([0.1, 0.9, 72.0]) == "raw"  # one outlier decides it
    assert catalog.score_scale([-0.5, 0.2]) == "raw"  # a score below zero is not a fraction
    assert catalog.score_scale([]) == "fraction"  # no scores yet: the common case


def test_the_provider_comparison_prefers_the_model_it_can_actually_compare(
    session: Session,
) -> None:
    """A brand-new release has usually been run against one thing, which compares to nothing."""
    catalog.upsert_models(
        session,
        [
            ModelUpsert(
                name="Newest Preview",
                organization="Anthropic",
                publication_date=date(2026, 6, 1),
                source="epoch_ai",
            ),
            ModelUpsert(
                name="Well Measured",
                organization="Anthropic",
                publication_date=date(2026, 1, 1),
                source="epoch_ai",
            ),
        ],
    )
    session.flush()
    known = catalog.known_model_ids(session)
    catalog.replace_benchmark_results(
        session, "GPQA Diamond", None, [("Newest Preview", 0.9, None, "E")], known
    )
    for name in ("GPQA Diamond", "MMLU"):
        catalog.replace_benchmark_results(
            session, name, None, [("Well Measured", 0.8, None, "E")], known
        )
    session.flush()

    config = parse_providers(
        {"benchmarks": ["gpqa-diamond", "mmlu"], "providers": [{"name": "Anthropic"}]}
    )
    entries, _ = catalog.provider_leaders(session, config)
    assert [entry.model_name for entry in entries] == ["Well Measured"]
    assert len(entries[0].scores) == 2


def test_a_sort_runs_in_both_directions_with_nulls_last_either_way(session: Session) -> None:
    """A heading that cannot toggle rebuilds the URL it is on, which reads as a broken sort."""
    catalog.upsert_models(
        session,
        [
            ModelUpsert(name="Small", parameters=1e9, source="epoch_ai"),
            ModelUpsert(name="Huge", parameters=1e12, source="epoch_ai"),
            ModelUpsert(name="Unknown", source="epoch_ai"),
        ],
    )
    session.flush()

    def names(**kwargs: object) -> list[str]:
        return [m.name for m in catalog.list_models(session, sort="parameters", **kwargs)]  # type: ignore[arg-type]

    assert names(descending=True) == ["Huge", "Small", "Unknown"]
    assert names(descending=False) == ["Small", "Huge", "Unknown"]
    # Unknown stays last ascending too: a missing parameter count is not a small one.
    assert names() == ["Huge", "Small", "Unknown"]  # the column's natural direction


def test_recent_provider_models_cap_window_and_attribution(session: Session) -> None:
    catalog.upsert_models(
        session,
        [
            ModelUpsert(
                name="GPT-6",
                organization="OpenAI",
                publication_date=date(2026, 7, 1),
                source="epoch_ai",
            ),
            ModelUpsert(
                name="GPT-5.5",
                organization="OpenAI",
                publication_date=date(2026, 5, 1),
                source="epoch_ai",
            ),
            ModelUpsert(
                name="GPT-5",
                organization="OpenAI",
                publication_date=date(2026, 3, 1),
                source="epoch_ai",
            ),
            # A collaboration filed as one comma-joined credit attributes to the lab.
            ModelUpsert(
                name="Phi-5",
                organization="Microsoft,University of Somewhere",
                publication_date=date(2026, 6, 1),
                source="epoch_ai",
            ),
            # Outside the window: recent is a claim, not a vibe.
            ModelUpsert(
                name="Old Thing",
                organization="OpenAI",
                publication_date=date(2024, 1, 1),
                source="epoch_ai",
            ),
            # Whole-part matching: the community fork belongs to nobody.
            ModelUpsert(
                name="Community Remix",
                organization="Mistral community",
                publication_date=date(2026, 6, 15),
                source="huggingface_models",
            ),
        ],
    )
    session.flush()

    config = parse_providers(
        {
            "providers": [
                {"name": "OpenAI"},
                {"name": "Microsoft"},
                {"name": "Mistral AI", "aliases": ["Mistral"]},
            ]
        }
    )
    items = catalog.recent_provider_models(session, config, since=date(2025, 8, 1), per_provider=2)

    assert [item.name for item in items] == ["GPT-6", "Phi-5", "GPT-5.5"]
    assert items[1].provider == "Microsoft"
    assert items[0].published_on == date(2026, 7, 1)


def test_headline_benchmarks_take_the_best_curated_score(session: Session) -> None:
    catalog.upsert_models(
        session,
        [
            ModelUpsert(
                name="Claude Opus 5",
                organization="Anthropic",
                publication_date=date(2026, 1, 1),
                source="epoch_ai",
            ),
            ModelUpsert(
                name="Qwen3 Max",
                organization="Qwen",
                publication_date=date(2026, 2, 1),
                source="epoch_ai",
            ),
            ModelUpsert(
                name="Nobody Special",
                organization="Unlisted Lab",
                publication_date=date(2026, 5, 1),
                source="epoch_ai",
            ),
        ],
    )
    session.flush()
    known = catalog.known_model_ids(session)
    catalog.replace_benchmark_results(
        session,
        "GPQA diamond",
        None,
        [
            ("Claude Opus 5", 0.91, None, "Epoch"),
            ("Qwen3 Max", 0.93, None, "Epoch"),
            # The global maximum, but not a curated lab: it must not hold the headline.
            ("Nobody Special", 0.99, None, "Epoch"),
        ],
        known,
    )
    catalog.replace_benchmark_results(
        session, "MMLU", None, [("Nobody Special", 0.99, None, "Epoch")], known
    )
    session.flush()

    config = parse_providers(
        {
            "benchmarks": ["mmlu", "gpqa-diamond"],
            "providers": [{"name": "Anthropic"}, {"name": "Alibaba", "aliases": ["Qwen"]}],
        }
    )
    items = catalog.headline_benchmarks(session, config)

    # Configured order, minus the benchmark only an unlisted lab scored.
    assert [(item.id, item.model_name, item.provider) for item in items] == [
        ("gpqa-diamond", "Qwen3 Max", "Alibaba")
    ]
    assert items[0].best_score == 0.93
    assert items[0].scale == "fraction"
    assert items[0].result_count == 3
