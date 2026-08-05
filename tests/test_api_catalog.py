"""The models and benchmarks routes.

Public, like the papers endpoints: this is published data about the world and none of it is
about the caller. The store is exercised against a real database in test_catalog.py; these pin
the HTTP shapes, the 404s, and the one field that makes these pages this site's rather than
anyone's - ``paper_id``.
"""

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import researchscout.api.routers.catalog as catalog_router
from researchscout.api.deps import get_session
from researchscout.api.main import create_app
from researchscout.store.catalog import (
    HeadlineBenchmark,
    ModelFilters,
    NotableModel,
    ScoreColumn,
)


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    return TestClient(app)


def _model(**overrides: object) -> SimpleNamespace:
    fields: dict[str, object] = {
        "id": "whisper",
        "name": "Whisper",
        "organization": "OpenAI",
        "publication_date": date(2022, 12, 6),
        "domains": "Speech,Language",
        "task": "automatic-speech-recognition",
        "parameters": 1.55e9,
        "training_compute_flop": 1.0e22,
        "accessibility": "Open weights (unrestricted)",
        "open_weights": True,
        "link": "https://arxiv.org/abs/2212.04356",
        "paper_id": "arxiv:2212.04356",
        "hf_repo": "openai/whisper-large-v3",
        "hf_downloads": 4_000_000,
        "hf_likes": 3_000,
        "sources": "epoch_ai",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _benchmark(**overrides: object) -> SimpleNamespace:
    fields: dict[str, object] = {
        "id": "gpqa-diamond",
        "name": "GPQA diamond",
        "released_on": date(2023, 11, 20),
        "result_count": 133,
        "score_scale": "fraction",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_models_list_carries_the_paper_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog_router.catalog, "list_models", lambda *a, **k: [_model()])
    monkeypatch.setattr(catalog_router.catalog, "count_models", lambda *a, **k: 1)
    body = _client().get("/v1/models").json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == "whisper"
    assert item["paper_id"] == "arxiv:2212.04356"
    # Comma-joined in the column, a list on the wire.
    assert item["domains"] == ["Speech", "Language"]
    assert item["sources"] == ["epoch_ai"]


def test_a_model_with_no_domains_reads_as_an_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        catalog_router.catalog, "list_models", lambda *a, **k: [_model(domains=None, sources="")]
    )
    monkeypatch.setattr(catalog_router.catalog, "count_models", lambda *a, **k: 1)
    item = _client().get("/v1/models").json()["items"][0]
    assert item["domains"] == []
    assert item["sources"] == []


def test_model_filters_reach_the_store(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_list(session: object, **kwargs: object) -> list:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(catalog_router.catalog, "list_models", fake_list)
    monkeypatch.setattr(catalog_router.catalog, "count_models", lambda *a, **k: 0)
    response = _client().get(
        "/v1/models",
        params={
            "q": "opus",
            "organization": "OpenAI",
            "domain": "Language",
            "open_weights": "true",
            "with_paper": "true",
            "sort": "parameters",
            "limit": 10,
            "offset": 20,
        },
    )
    assert response.status_code == 200
    # One object carrying every filter, so the list and the count cannot be given different ones.
    filters = seen["filters"]
    assert isinstance(filters, ModelFilters)
    assert filters.organization == "OpenAI"
    assert filters.domain == "Language"
    assert filters.open_weights is True
    assert filters.with_paper is True
    assert filters.query == "opus"
    assert seen["sort"] == "parameters"
    assert (seen["limit"], seen["offset"]) == (10, 20)


def test_the_count_is_given_the_same_filters_as_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """A count that filters differently from its list is a pager onto empty pages."""
    given: list[object] = []

    monkeypatch.setattr(
        catalog_router.catalog,
        "list_models",
        lambda session, **kwargs: given.append(kwargs["filters"]) or [],
    )
    monkeypatch.setattr(
        catalog_router.catalog,
        "count_models",
        lambda session, **kwargs: given.append(kwargs["filters"]) or 0,
    )
    _client().get("/v1/models", params={"organization": "OpenAI", "with_paper": "true"})

    assert len(given) == 2
    assert given[0] == given[1]


def test_an_unknown_sort_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in a sort key should say so rather than silently ordering by something else."""
    monkeypatch.setattr(catalog_router.catalog, "list_models", lambda *a, **k: [])
    monkeypatch.setattr(catalog_router.catalog, "count_models", lambda *a, **k: 0)
    assert _client().get("/v1/models", params={"sort": "populariy"}).status_code == 422


def test_asking_for_one_paper_lists_what_came_out_of_it(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_for_paper(session: object, paper_id: str) -> list:
        seen["paper_id"] = paper_id
        return [_model()]

    monkeypatch.setattr(catalog_router.catalog, "models_for_paper", fake_for_paper)
    body = _client().get("/v1/models", params={"paper_id": "arxiv:2212.04356"}).json()
    assert seen["paper_id"] == "arxiv:2212.04356"
    assert [item["name"] for item in body["items"]] == ["Whisper"]
    assert body["total"] == 1


def test_a_model_detail_carries_its_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog_router.catalog, "get_model", lambda *a, **k: _model())
    monkeypatch.setattr(
        catalog_router.catalog, "results_for_model", lambda *a, **k: [("MMLU", 0.76, "fraction")]
    )
    body = _client().get("/v1/models/whisper").json()
    assert body["scores"] == [
        {
            "benchmark": "MMLU",
            "model": "Whisper",
            "model_id": "whisper",
            "score": 0.76,
            "measured_on": None,
            "origin": None,
            "scale": "fraction",
        }
    ]


def test_an_unknown_model_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog_router.catalog, "get_model", lambda *a, **k: None)
    assert _client().get("/v1/models/nope").status_code == 404


def test_benchmarks_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog_router.catalog, "list_benchmarks", lambda *a, **k: [_benchmark()])
    body = _client().get("/v1/benchmarks").json()
    assert body["items"] == [
        {
            "id": "gpqa-diamond",
            "name": "GPQA diamond",
            "released_on": "2023-11-20",
            "result_count": 133,
            "score_scale": "fraction",
        }
    ]


def test_a_benchmark_detail_is_a_leaderboard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog_router.catalog, "get_benchmark", lambda *a, **k: _benchmark())
    monkeypatch.setattr(
        catalog_router.catalog,
        "leaderboard",
        lambda *a, **k: [
            SimpleNamespace(
                model_name="Gemini 3.6 Flash",
                model_id=None,
                score=0.922,
                measured_on=date(2026, 2, 1),
                origin="Epoch",
            ),
            SimpleNamespace(
                model_name="Claude Opus 5",
                model_id="claude-opus-5",
                score=0.918,
                measured_on=None,
                origin="Epoch",
            ),
        ],
    )
    body = _client().get("/v1/benchmarks/gpqa-diamond").json()
    assert body["name"] == "GPQA diamond"
    assert [(r["model"], r["model_id"]) for r in body["results"]] == [
        ("Gemini 3.6 Flash", None),  # not in the catalogue, still on the board
        ("Claude Opus 5", "claude-opus-5"),
    ]


def test_an_unknown_benchmark_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog_router.catalog, "get_benchmark", lambda *a, **k: None)
    assert _client().get("/v1/benchmarks/nope").status_code == 404


def test_the_provider_comparison_carries_its_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    """The columns come back beside the rows: a score is keyed by benchmark, not positional."""
    entry = SimpleNamespace(
        provider="Anthropic",
        country="United States",
        model_id="claude-opus-5",
        model_name="Claude Opus 5",
        published_on=date(2026, 1, 1),
        paper_id=None,
        open_weights=False,
        scores={"gpqa-diamond": 0.91},
    )
    monkeypatch.setattr(
        catalog_router.catalog,
        "provider_leaders",
        lambda *a, **k: (
            [entry],
            [ScoreColumn(id="gpqa-diamond", name="GPQA Diamond", scale="fraction")],
        ),
    )
    body = _client().get("/v1/providers").json()

    assert body["columns"] == [{"id": "gpqa-diamond", "name": "GPQA Diamond", "scale": "fraction"}]
    item = body["items"][0]
    assert item["provider"] == "Anthropic"
    assert item["country"] == "United States"
    assert item["model_name"] == "Claude Opus 5"
    assert item["scores"] == {"gpqa-diamond": 0.91}


def test_an_unconfigured_comparison_is_empty_rather_than_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(catalog_router.catalog, "provider_leaders", lambda *a, **k: ([], []))
    body = _client().get("/v1/providers").json()
    assert body == {"columns": [], "items": []}


def test_notable_is_a_route_not_a_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """/models/notable must resolve before the dynamic model route swallows it."""
    monkeypatch.setattr(
        catalog_router.catalog,
        "recent_provider_models",
        lambda session, config, *, since: [
            NotableModel(
                id="gpt-6",
                name="GPT-6",
                provider="OpenAI",
                country="United States",
                published_on=date(2026, 7, 1),
                parameters=2.0e12,
                open_weights=False,
            )
        ],
    )
    body = _client().get("/v1/models/notable").json()
    item = body["items"][0]
    assert item["name"] == "GPT-6"
    assert item["provider"] == "OpenAI"
    assert item["published_on"] == "2026-07-01"


def test_headline_is_a_route_not_a_benchmark_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        catalog_router.catalog,
        "headline_benchmarks",
        lambda session, config: [
            HeadlineBenchmark(
                id="gpqa-diamond",
                name="GPQA Diamond",
                scale="fraction",
                result_count=133,
                best_score=0.93,
                model_id="qwen3-max",
                model_name="Qwen3 Max",
                provider="Alibaba",
            )
        ],
    )
    body = _client().get("/v1/benchmarks/headline").json()
    item = body["items"][0]
    assert item["id"] == "gpqa-diamond"
    assert item["best_score"] == 0.93
    assert item["provider"] == "Alibaba"
