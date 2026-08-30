from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from openai import OpenAIError

import researchscout.api.routers.ask as ask_router
import researchscout.api.routers.papers as papers_router
from researchscout.answer import Answer
from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_embedder, get_llm, get_session
from researchscout.api.main import create_app
from researchscout.retrieve.search import ScoredPaper
from researchscout.schema import Author, Paper
from researchscout.store.facets import PaperFacets


def _paper(pid: str = "arxiv:2401.00001", title: str = "T") -> Paper:
    return Paper(
        id=pid,
        title=title,
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source="arxiv",
    )


@pytest.fixture(autouse=True)
def _capture_ask_metrics(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Metrics recording opens its own DB session; keep unit tests hermetic."""
    rows: list[dict] = []
    monkeypatch.setattr(ask_router, "record_metrics", lambda **kw: rows.append(kw))
    return rows


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[get_embedder] = lambda: None
    app.dependency_overrides[get_llm] = lambda: None
    app.dependency_overrides[require_user] = lambda: User(sub="tester", username="tester")
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_dismissals(monkeypatch: pytest.MonkeyPatch) -> None:
    """The feed reads the caller's dismissals, and the session here is a stub.

    These tests are about forwarding and response shapes, so the read is stubbed out rather
    than given a database; the exclusion itself is pinned separately below.
    """
    monkeypatch.setattr(papers_router, "dismissed_papers", lambda *a, **k: [])


def test_healthz() -> None:
    response = _client().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_papers_lists_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(papers_router, "list_papers", lambda *a, **k: [_paper()])
    monkeypatch.setattr(papers_router, "count_papers", lambda *a, **k: 1)
    response = _client().get("/v1/papers")
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == ["arxiv:2401.00001"]
    assert body["items"][0]["score"] is None
    assert body["total"] == 1


def test_papers_query_ranks(monkeypatch: pytest.MonkeyPatch) -> None:
    scored = ScoredPaper(paper=_paper(), score=0.9, distance=0.1)
    seen: dict[str, object] = {}

    def fake_retrieve(session: object, embedder: object, q: str, **kwargs: object) -> list:
        seen["q"] = q
        seen.update(kwargs)
        return [scored]

    monkeypatch.setattr(papers_router, "retrieve", fake_retrieve)
    response = _client().get("/v1/papers", params={"q": "state space models", "limit": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["score"] == 0.9
    assert body["total"] is None  # search has no unpaginated count
    assert seen["q"] == "state space models"
    assert seen["k"] == 5
    assert seen["use_rerank"] is False  # the feed stays on the fast first-stage path


def test_papers_forwards_facets_and_sort(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_list(session: object, **kwargs: object) -> list:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(papers_router, "list_papers", fake_list)
    monkeypatch.setattr(papers_router, "count_papers", lambda *a, **k: 0)
    response = _client().get(
        "/v1/papers",
        params=[
            ("subject", "ai"),
            ("subject", "stats"),
            ("topic", "rl"),
            ("category", "cs.LG"),
            ("category", "math.CO"),
            ("year", "2024"),
            ("month", "1"),
            ("author", "lovelace"),
            ("venue", "neurips"),
            ("min_citations", "5"),
            ("sort", "citations"),
        ],
    )
    assert response.status_code == 200
    facets = seen["facets"]
    assert isinstance(facets, PaperFacets)
    assert facets.subjects == ["ai", "stats"]
    assert facets.topics == ["rl"]
    assert facets.categories == ["cs.LG", "math.CO"]
    assert (facets.year, facets.month) == (2024, 1)
    assert facets.author == "lovelace"
    assert facets.venue == "neurips"
    assert facets.min_citations == 5
    assert seen["sort"] == "citations"


def test_papers_query_forwards_facets(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_retrieve(session: object, embedder: object, q: str, **kwargs: object) -> list:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(papers_router, "retrieve", fake_retrieve)
    response = _client().get("/v1/papers", params={"q": "ssm", "subject": "physical"})
    assert response.status_code == 200
    facets = seen["facets"]
    assert isinstance(facets, PaperFacets)
    assert facets.subjects == ["physical"]


def test_papers_subject_forwards_on_both_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_list(session: object, **kwargs: object) -> list:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(papers_router, "list_papers", fake_list)
    monkeypatch.setattr(papers_router, "count_papers", lambda *a, **k: 0)
    assert _client().get("/v1/papers", params={"subject": "ai"}).status_code == 200
    facets = seen["facets"]
    assert isinstance(facets, PaperFacets)
    assert facets.subjects == ["ai"]

    seen.clear()

    def fake_retrieve(session: object, embedder: object, q: str, **kwargs: object) -> list:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(papers_router, "retrieve", fake_retrieve)
    assert _client().get("/v1/papers", params={"q": "ssm", "subject": "ai"}).status_code == 200
    facets = seen["facets"]
    assert isinstance(facets, PaperFacets)
    assert facets.subjects == ["ai"]


def test_papers_bogus_subject_is_422() -> None:
    # Named rather than silently empty: "no such subject" and "no papers" are different answers.
    response = _client().get("/v1/papers", params={"subject": "banana"})
    assert response.status_code == 422
    assert "banana" in response.json()["detail"]


def test_papers_bogus_topic_is_422() -> None:
    assert _client().get("/v1/papers", params={"topic": "banana"}).status_code == 422


def test_month_without_year_is_422() -> None:
    assert _client().get("/v1/papers", params={"month": 3}).status_code == 422


def test_days_with_year_is_422() -> None:
    assert _client().get("/v1/papers", params={"days": 7, "year": 2024}).status_code == 422


def test_paper_detail_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(papers_router, "get_paper", lambda *a, **k: None)
    assert _client().get("/v1/papers/arxiv:0000.00000").status_code == 404


def test_paper_detail_allows_slashes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def fake_get_paper(session: object, paper_id: str) -> Paper:
        seen["id"] = paper_id
        return _paper(pid=paper_id)

    monkeypatch.setattr(papers_router, "get_paper", fake_get_paper)
    # The detail response computes the paper's momentum, and the session here is a stub.
    monkeypatch.setattr(papers_router, "breakthrough", lambda *a, **k: _NoBoost())
    response = _client().get("/v1/papers/doi:10.1145/3600006.3613165")
    assert response.status_code == 200
    assert seen["id"] == "doi:10.1145/3600006.3613165"


class _NoBoost:
    total = 0.0


def test_ask_returns_grounded_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    used = ScoredPaper(paper=_paper(), score=1.0, distance=0.0)
    result = Answer(
        text="See [arxiv:2401.00001].",
        cited=["arxiv:2401.00001"],
        hallucinated=["arxiv:9999.99999"],
        used=[used],
    )
    monkeypatch.setattr(ask_router, "answer", lambda *a, **k: result)
    response = _client().post("/v1/ask", json={"question": "what is new?"})
    assert response.status_code == 200
    body = response.json()
    assert body["cited"] == ["arxiv:2401.00001"]
    assert body["hallucinated"] == ["arxiv:9999.99999"]
    assert body["used"][0]["id"] == "arxiv:2401.00001"


def test_ask_rejects_empty_question() -> None:
    assert _client().post("/v1/ask", json={"question": ""}).status_code == 422


def test_ask_maps_llm_failure_to_502(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> Answer:
        raise OpenAIError("connection refused")

    monkeypatch.setattr(ask_router, "answer", boom)
    response = _client().post("/v1/ask", json={"question": "q"})
    assert response.status_code == 502
    assert response.json()["detail"] == "LLM backend unavailable"


def test_ask_names_a_spent_quota(
    monkeypatch: pytest.MonkeyPatch, _capture_ask_metrics: list[dict]
) -> None:
    class _Quota(OpenAIError):
        status_code = 429

    def boom(*a: object, **k: object) -> Answer:
        raise _Quota("Rate limit exceeded: free-models-per-day")

    monkeypatch.setattr(ask_router, "answer", boom)
    response = _client().post("/v1/ask", json={"question": "q"})
    assert response.status_code == 502
    assert response.json()["detail"] == "LLM quota exhausted for today"
    assert _capture_ask_metrics[0]["outcome"] == "llm_error"


def test_ask_records_timings_and_identity(
    monkeypatch: pytest.MonkeyPatch, _capture_ask_metrics: list[dict]
) -> None:
    from researchscout.api.auth import owner_tag

    used = ScoredPaper(paper=_paper(), score=1.0, distance=0.0)

    def fake_answer(*a: object, **k: object) -> Answer:
        timings = k.get("timings")
        if isinstance(timings, dict):
            timings.update({"retrieve_ms": 80.0, "llm_ms": 700.0})
        return Answer(
            text="See [arxiv:2401.00001].",
            cited=["arxiv:2401.00001"],
            hallucinated=[],
            used=[used],
            model="test-model",
            prompt_tokens=100,
            completion_tokens=25,
            plan=["alpha", "beta"],
        )

    monkeypatch.setattr(ask_router, "answer", fake_answer)
    response = _client().post("/v1/ask", json={"question": "q", "agentic": True})
    assert response.status_code == 200
    assert response.json()["plan"] == ["alpha", "beta"]
    row = _capture_ask_metrics[0]
    assert row["outcome"] == "ok"
    assert row["retrieve_ms"] == 80 and row["llm_ms"] == 700
    assert row["model"] == "test-model"
    assert row["prompt_tokens"] == 100 and row["completion_tokens"] == 25
    assert row["agentic"] is True
    # The test app runs in local no-auth mode, so the resolved account is the local user.
    assert row["user_hash"] == owner_tag("local")


def test_the_feed_leaves_out_what_the_caller_dismissed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dismissing sends a paper out of the feed, and a reload has to keep it out.

    Applied as a filter rather than by dropping rows from the response, so the total and the
    pager describe the page that was actually built.
    """
    seen: dict[str, PaperFacets] = {}

    def capture(session: object, **kwargs: object) -> list[Paper]:
        seen["list"] = kwargs["facets"]  # type: ignore[assignment]
        return []

    monkeypatch.setattr(papers_router, "list_papers", capture)
    monkeypatch.setattr(
        papers_router,
        "count_papers",
        lambda session, facets: seen.setdefault("count", facets) and 0,
    )
    monkeypatch.setattr(papers_router, "dismissed_papers", lambda *a, **k: ["arxiv:2401.00002"])

    assert _client().get("/v1/papers").status_code == 200
    assert seen["list"].exclude == ["arxiv:2401.00002"]
    # The count has to filter identically or the pager promises pages that are not there.
    assert seen["count"].exclude == ["arxiv:2401.00002"]


def test_a_search_still_finds_a_dismissed_paper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dismissing is "not in what is new", not "hide this from me"."""
    seen: dict[str, PaperFacets] = {}

    def capture(session: object, embedder: object, query: str, **kwargs: object) -> list:
        seen["facets"] = kwargs["facets"]  # type: ignore[assignment]
        return []

    monkeypatch.setattr(papers_router, "retrieve", capture)
    monkeypatch.setattr(papers_router, "dismissed_papers", lambda *a, **k: ["arxiv:2401.00002"])

    assert _client().get("/v1/papers", params={"q": "transformers"}).status_code == 200
    assert seen["facets"].exclude is None
