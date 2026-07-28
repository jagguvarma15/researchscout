import math
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

import researchscout.retrieve.search as search_mod
from researchscout.embed.base import Embedder
from researchscout.retrieve.search import retrieve
from researchscout.schema import Author, Paper
from researchscout.score import Breakthrough


class StubEmbedder(Embedder):
    model_id = "stub-v1"
    dim = 3

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0]


def _paper(pid: str) -> Paper:
    return Paper(
        id=pid,
        title="T",
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=datetime.now(UTC),
        source="arxiv",
    )


def _session() -> Any:
    return SimpleNamespace(rollback=lambda: None)


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    vector: list[tuple[str, float]],
    lexical: list[tuple[str, float]],
    citations: dict[str, float] | None = None,
) -> None:
    monkeypatch.setattr(search_mod, "vector_search", lambda *a, **k: vector)
    monkeypatch.setattr(search_mod, "lexical_search", lambda *a, **k: lexical)
    monkeypatch.setattr(search_mod, "get_papers", lambda s, ids: {pid: _paper(pid) for pid in ids})
    cites = citations or {}
    monkeypatch.setattr(
        search_mod,
        "breakthrough_many",
        lambda s, ids: {
            pid: Breakthrough(total=math.log1p(cites.get(pid, 0.0)), contributions={})
            for pid in ids
        },
    )


def test_rrf_prefers_papers_found_by_both_legs(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(
        monkeypatch,
        vector=[("arxiv:1", 0.1), ("arxiv:2", 0.2)],
        lexical=[("arxiv:3", 5.0), ("arxiv:1", 4.0)],
    )
    results = retrieve(_session(), StubEmbedder(), "q", k=10, days=30)
    ids = [item.paper.id for item in results]
    # arxiv:1 appears in both legs (1/61 + 1/62) and beats each single-leg paper (1/61, 1/62).
    assert ids == ["arxiv:1", "arxiv:3", "arxiv:2"]


def test_authority_boost_outranks_uncited_equal(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(
        monkeypatch,
        vector=[("arxiv:uncited", 0.1), ("arxiv:cited", 0.11)],
        lexical=[],
        citations={"arxiv:cited": 50.0},
    )
    results = retrieve(_session(), StubEmbedder(), "q", k=10, days=30)
    # The citation prior (1 + log1p(50) ~ 4.9x) overcomes one rank step of RRF.
    assert [item.paper.id for item in results] == ["arxiv:cited", "arxiv:uncited"]


def test_lexical_only_hit_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(monkeypatch, vector=[("arxiv:1", 0.1)], lexical=[("arxiv:2", 3.0)])
    results = retrieve(_session(), StubEmbedder(), "q", k=10, days=30)
    by_id = {item.paper.id: item for item in results}
    assert set(by_id) == {"arxiv:1", "arxiv:2"}
    # A lexical-only hit has no measured cosine distance and reports the maximum.
    assert by_id["arxiv:2"].distance == 1.0
    assert by_id["arxiv:1"].distance == 0.1


def test_lexical_errors_fall_back_to_vector_only(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> list[tuple[str, float]]:
        raise SQLAlchemyError("tsquery exploded")

    rolled_back: list[bool] = []
    _setup(monkeypatch, vector=[("arxiv:1", 0.1)], lexical=[])
    monkeypatch.setattr(search_mod, "lexical_search", boom)
    session = SimpleNamespace(rollback=lambda: rolled_back.append(True))
    results = retrieve(session, StubEmbedder(), "q", k=10, days=30)
    assert [item.paper.id for item in results] == ["arxiv:1"]
    assert rolled_back == [True]


def test_use_rerank_false_never_touches_the_reranker(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(monkeypatch, vector=[("arxiv:1", 0.1)], lexical=[])

    def boom() -> None:
        raise AssertionError("get_reranker must not be called when use_rerank is False")

    monkeypatch.setattr(search_mod, "get_reranker", boom)
    results = retrieve(_session(), StubEmbedder(), "q", k=5, days=30, use_rerank=False)
    assert [item.paper.id for item in results] == ["arxiv:1"]


def test_k_truncates_after_fusion(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(
        monkeypatch,
        vector=[(f"arxiv:{i}", 0.1 * i) for i in range(1, 6)],
        lexical=[],
    )
    results = retrieve(_session(), StubEmbedder(), "q", k=2, days=30)
    assert len(results) == 2
