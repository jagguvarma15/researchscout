import logging
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

import researchscout.answer as answer_mod
from researchscout.answer import answer
from researchscout.llm.base import LLM
from researchscout.retrieve.search import ScoredPaper
from researchscout.schema import Author, Paper
from researchscout.trace import trace_span


class FakeLLM(LLM):
    model = "fake"

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.last_user = ""

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        self.last_user = user
        return self._reply


class _StubEmbedder:
    model_id = "stub"
    dim = 3

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0]


def _scored_rel(
    pid: str,
    *,
    relevance: float | None = None,
    distance: float = 0.0,
    keywords: list[str] | None = None,
) -> ScoredPaper:
    item = _scored(pid)
    item.relevance = relevance
    item.distance = distance
    if keywords:
        item.paper.keywords = keywords
    return item


def _scored(pid: str, title: str = "T", abstract: str = "A") -> ScoredPaper:
    paper = Paper(
        id=pid,
        external_ids={"arxiv": pid.split(":")[1]},
        title=title,
        abstract=abstract,
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source="arxiv",
    )
    return ScoredPaper(paper=paper, score=1.0, distance=0.0)


@pytest.fixture(autouse=True)
def _hermetic_chunk_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic against a local .env that enables chunk retrieval (tests opt in explicitly)."""
    monkeypatch.setenv("RS_CHUNK_RETRIEVAL", "0")


def test_answer_cites_only_retrieved(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored("arxiv:2401.00001"), _scored("arxiv:2401.00002")]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)
    llm = FakeLLM("Great [arxiv:2401.00001] but also invented [arxiv:9999.99999].")
    result = answer(None, _StubEmbedder(), llm, "q")
    assert result.cited == ["arxiv:2401.00001"]
    assert result.hallucinated == ["arxiv:9999.99999"]


def test_prompt_contains_retrieved_context(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored("arxiv:2401.00001", title="Cool Paper", abstract="An abstract here.")]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)
    llm = FakeLLM("[arxiv:2401.00001]")
    answer(None, _StubEmbedder(), llm, "what is cool?")
    assert "[arxiv:2401.00001]" in llm.last_user
    assert "Cool Paper" in llm.last_user
    assert "An abstract here." in llm.last_user


def test_prompt_carries_enrichment_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    from researchscout.schema import PaperLabel

    enriched = _scored("arxiv:2401.00001")
    enriched.paper = enriched.paper.model_copy(
        update={
            "keywords": ["state space models", "long context"],
            "sections": [f"S{i}" for i in range(1, 11)],  # capped at 8 in the prompt
            "labels": [PaperLabel(label="efficiency", source="custom")],
        }
    )
    plain = _scored("arxiv:2401.00002")
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: [enriched, plain])
    llm = FakeLLM("[arxiv:2401.00001]")
    answer(None, _StubEmbedder(), llm, "q")

    assert "Keywords: state space models, long context" in llm.last_user
    assert "Sections: S1; S2" in llm.last_user
    assert "S8" in llm.last_user and "S9" not in llm.last_user
    assert "Labels: efficiency" in llm.last_user
    # The unenriched paper's block gains no empty enrichment lines.
    plain_block = llm.last_user.split("[arxiv:2401.00002]")[1]
    assert "Keywords:" not in plain_block and "Labels:" not in plain_block


def test_answer_empty_when_no_papers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: [])
    result = answer(None, _StubEmbedder(), FakeLLM("unused"), "q")
    assert result.used == []
    assert result.cited == []


def test_trace_span_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="researchscout.trace"):
        with trace_span("unit", foo=1) as span:
            span["bar"] = 2
    assert any("unit" in record.getMessage() for record in caplog.records)


def test_answer_fast_composes_extractive_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored_rel("arxiv:2401.00001", relevance=0.85, keywords=["sparse attention"])]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)
    fast = answer_mod.answer_fast(None, _StubEmbedder(), "sparse attention transformers")

    assert fast.found and fast.best_relevance == 0.85
    text = fast.answer.text
    assert "Found 1 recent paper matching your question." in text
    assert "[arxiv:2401.00001]" in text
    assert "Matches: sparse, attention" in text
    assert "Keywords: sparse attention" in text
    assert fast.answer.cited == ["arxiv:2401.00001"]
    assert fast.answer.hallucinated == []

    # The text derives from the structured entries, so both carry the same facts.
    assert len(fast.entries) == 1
    entry = fast.entries[0]
    assert entry.id == "arxiv:2401.00001"
    assert entry.title == "T" and entry.venue is None
    assert entry.published_at == datetime(2024, 1, 1, tzinfo=UTC)
    assert entry.matches == ["sparse", "attention"]
    assert entry.keywords == ["sparse attention"]
    assert entry.excerpt is None  # chunk retrieval is off in unit tests
    assert entry.relevance == 0.85


def test_answer_fast_not_found_carries_no_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored_rel("arxiv:2401.00001", relevance=0.05)]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)
    fast = answer_mod.answer_fast(None, _StubEmbedder(), "q")
    assert not fast.found and fast.entries == []


def test_answer_fast_below_floor_reports_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored_rel("arxiv:2401.00001", relevance=0.05)]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)
    fast = answer_mod.answer_fast(None, _StubEmbedder(), "q")

    assert not fast.found
    assert fast.best_relevance == 0.05
    assert fast.answer.used == [] and fast.answer.cited == []


def test_answer_fast_cosine_fallback_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RS_ASK_MIN_SIMILARITY", "0.68")
    close = [_scored_rel("arxiv:2401.00001", distance=0.2)]  # similarity 0.8
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: close)
    assert answer_mod.answer_fast(None, _StubEmbedder(), "q").found

    far = [_scored_rel("arxiv:2401.00001", distance=0.38)]  # similarity 0.62
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: far)
    assert not answer_mod.answer_fast(None, _StubEmbedder(), "q").found


def test_answer_fast_floor_follows_the_evidence_scale(monkeypatch: pytest.MonkeyPatch) -> None:
    # 0.35 clears the cross-encoder floor (0.30) even though it would fail the cosine one;
    # the calibrated scale wins because the reranker actually scored the hit.
    monkeypatch.setenv("RS_ASK_MIN_SIMILARITY", "0.68")
    scored = [_scored_rel("arxiv:2401.00001", relevance=0.35)]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: scored)
    assert answer_mod.answer_fast(None, _StubEmbedder(), "q").found


def test_answer_fast_rerank_flag_reaches_retrieve(
    monkeypatch: pytest.MonkeyPatch, set_setting: Callable[[str, str], None]
) -> None:
    seen: list[object] = []

    def capture(*a: object, **k: object) -> list[ScoredPaper]:
        seen.append(k["use_rerank"])
        return []

    monkeypatch.setattr(answer_mod, "retrieve", capture)
    # set_setting rather than setenv: settings are read once per process, so flipping the flag
    # between the two calls needs the cache dropped the way a restart would.
    set_setting("RS_ASK_FAST_RERANK", "1")
    answer_mod.answer_fast(None, _StubEmbedder(), "q")
    set_setting("RS_ASK_FAST_RERANK", "0")
    answer_mod.answer_fast(None, _StubEmbedder(), "q")
    assert seen == [True, False]


def test_answer_fast_never_false_negatives_on_unknowable_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A lexical-only hit carries the sentinel distance: relevance unknowable, presence wins.
    lexical = [_scored_rel("arxiv:2401.00001", distance=1.0)]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: lexical)
    fast = answer_mod.answer_fast(None, _StubEmbedder(), "q")
    assert fast.found and fast.best_relevance is None

    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: [])
    empty = answer_mod.answer_fast(None, _StubEmbedder(), "q")
    assert not empty.found and empty.best_relevance is None
