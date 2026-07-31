from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import researchscout.stream.categorize as categorize_mod
from researchscout.embed.base import Embedder
from researchscout.llm.base import LLM
from researchscout.stream.categorize import (
    Categorizer,
    LabelSpec,
    extract_keywords,
    keyword_candidates,
    load_labels,
)
from researchscout.stream.envelope import Envelope

DOC = [1.0, 0.0, 0.0]


class FakeEmbedder(Embedder):
    model_id = "fake"
    dim = 3

    def __init__(self, table: dict[str, list[float]] | None = None) -> None:
        self._table = table or {}

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._table.get(text, [0.0, 0.0, 1.0]) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class FakeLLM(LLM):
    model = "fake"

    def __init__(self, reply: str = "", error: bool = False) -> None:
        self._reply = reply
        self._error = error
        self.calls = 0

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        self.calls += 1
        if self._error:
            raise RuntimeError("llm down")
        return self._reply


@contextmanager
def _no_session() -> Iterator[None]:
    yield None


def _categorizer(
    embedder: FakeEmbedder,
    llm: FakeLLM,
    *,
    fallback: bool = True,
    labels: list[LabelSpec] | None = None,
) -> Categorizer:
    return Categorizer(
        embedder,
        llm,
        _no_session,
        topic_match_min=0.55,
        keyword_min_similarity=0.35,
        keywords_llm_fallback=fallback,
        labels=labels or [],
    )


def _paper_envelope(title: str = "Sparse attention", abstract: str = "") -> Envelope:
    abstract = abstract or "sparse attention speeds transformer inference remarkably well today"
    return Envelope(
        kind="paper",
        source="arxiv",
        fetched_at=datetime(2026, 7, 30, tzinfo=UTC),
        payload={
            "paper": {
                "id": "arxiv:2607.1",
                "title": title,
                "abstract": abstract,
                "primary_category": "cs.LG",
            }
        },
    )


def _table(title: str, abstract: str) -> dict[str, list[float]]:
    return {
        f"{title}\n\n{abstract}": DOC,
        "sparse attention": [0.9, 0.0, 0.1],
        "transformer inference": [0.8, 0.1, 0.0],
    }


def test_extract_keywords_scores_and_diversifies() -> None:
    # The doc sits between two directions; the near-duplicate of the first pick is highly
    # redundant while the orthogonal phrase matches the doc equally well, so MMR demotes
    # the duplicate. Everything below the similarity floor never appears.
    doc = [0.7, 0.7, 0.0]
    table = {
        "sparse attention": [0.95, 0.1, 0.0],  # sim 0.735, picked first
        "sparse attentions": [0.9, 0.1, 0.0],  # sim 0.70, redundancy 0.865
        "kernel fusion": [0.1, 0.9, 0.0],  # sim 0.70, redundancy 0.185
        "yesterday": [0.0, 0.1, 0.9],  # sim 0.07, under the floor
    }
    picked = extract_keywords(
        "sparse attention sparse attentions kernel fusion yesterday",
        doc,
        FakeEmbedder(table),
        min_similarity=0.35,
        top_k=2,
    )
    assert picked[0][0] == "sparse attention"
    assert picked[1][0] == "kernel fusion"  # diversity beat the near duplicate


def test_keyword_candidates_cap_keeps_the_most_frequent() -> None:
    text = "alpha alpha alpha beta beta gamma"
    capped = keyword_candidates(text, cap=2)
    assert capped == ["alpha", "alpha alpha"]  # top 2 by in-document frequency
    assert "gamma" in keyword_candidates(text, cap=80)
    assert keyword_candidates("the and of", cap=80) == []  # all stop words


def test_extract_keywords_honors_the_candidate_cap() -> None:
    # With the cap at 1 only the most frequent term is ever embedded or selectable.
    doc = [1.0, 0.0, 0.0]
    table = {"kernel": [0.9, 0.0, 0.1], "fusion": [0.9, 0.1, 0.0]}
    picked = extract_keywords(
        "kernel kernel fusion", doc, FakeEmbedder(table), cap=1, min_similarity=0.35
    )
    assert [phrase for phrase, _ in picked] == ["kernel"]


def test_run_enriches_a_paper_packet(monkeypatch: pytest.MonkeyPatch) -> None:
    envelope = _paper_envelope()
    table = _table("Sparse attention", envelope.payload["paper"]["abstract"])
    topic_row = SimpleNamespace(id=7, topic_key="t-1", label="Efficient attention", centroid=DOC)
    monkeypatch.setattr(categorize_mod, "list_topics", lambda session: [topic_row])

    llm = FakeLLM("unused")
    result = _categorizer(FakeEmbedder(table), llm, fallback=False).run(envelope)

    assert result.vector == DOC
    enrichment = envelope.payload["enrichment"]
    assert enrichment["group"] == "cs" and enrichment["tech"] is True
    assert enrichment["topic"] == {"key": "t-1", "label": "Efficient attention", "similarity": 1.0}
    assert enrichment["keywords"] == ["sparse attention", "transformer inference"]
    assert enrichment["keyword_method"] == "statistical"
    assert enrichment["labels"] == []
    assert llm.calls == 0
    assert envelope.lineage[-1].outcome == "ok"
    detail = envelope.lineage[-1].detail
    assert detail is not None
    assert detail["keyword_method"] == "statistical"
    assert detail["candidate_count"] > 0
    assert detail["topic_score"] == 1.0


def _doc_only_table(envelope: Envelope) -> dict[str, list[float]]:
    """The document embeds along x; every candidate keeps the default z vector (sim 0)."""
    paper = envelope.payload["paper"]
    return {f"{paper['title']}\n\n{paper['abstract']}": DOC}


def test_weak_extraction_falls_back_to_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(categorize_mod, "list_topics", lambda session: [])
    envelope = _paper_envelope()
    llm = FakeLLM("graph neural networks, molecule design, chemistry")
    result = _categorizer(FakeEmbedder(_doc_only_table(envelope)), llm).run(envelope)

    enrichment = envelope.payload["enrichment"]
    assert enrichment["keyword_method"] == "llm"
    assert enrichment["keywords"] == ["graph neural networks", "molecule design", "chemistry"]
    assert enrichment["topic"] is None
    assert result.vector == DOC


def test_llm_failure_keeps_the_statistical_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(categorize_mod, "list_topics", lambda session: [])
    envelope = _paper_envelope()
    embedder = FakeEmbedder(_doc_only_table(envelope))
    result = _categorizer(embedder, FakeLLM(error=True)).run(envelope)

    enrichment = envelope.payload["enrichment"]
    assert enrichment["keyword_method"] == "statistical"
    assert enrichment["keywords"] == []
    assert envelope.lineage[-1].outcome == "ok"  # a fallback failure is not a stage failure
    assert result.vector is not None


def test_custom_labels_filter_to_the_configured_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(categorize_mod, "list_topics", lambda session: [])
    envelope = _paper_envelope()
    table = _table("Sparse attention", envelope.payload["paper"]["abstract"])
    labels = [LabelSpec("efficiency", ""), LabelSpec("safety", "")]
    llm = FakeLLM("Efficiency, made-up-label, safety")
    _categorizer(FakeEmbedder(table), llm, fallback=False, labels=labels).run(envelope)

    assert envelope.payload["enrichment"]["labels"] == ["efficiency", "safety"]


class CountingEmbedder(FakeEmbedder):
    def __init__(self, table: dict[str, list[float]] | None = None) -> None:
        super().__init__(table)
        self.calls = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return super().embed_documents(texts)


class FlakyEmbedder(FakeEmbedder):
    """Fails the first call (the merged batch embed), then behaves."""

    def __init__(self, table: dict[str, list[float]] | None = None) -> None:
        super().__init__(table)
        self.failed = False

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not self.failed:
            self.failed = True
            raise RuntimeError("mps hiccup")
        return super().embed_documents(texts)


def test_run_batch_merges_embeds_and_preserves_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(categorize_mod, "list_topics", lambda session: [])
    first = _paper_envelope()
    second = _paper_envelope("Kernel fusion", "kernel fusion compiles graphs into fused kernels")
    signal = Envelope(
        kind="signal", source="s2", fetched_at=datetime(2026, 7, 31, tzinfo=UTC), payload={}
    )
    table = {
        **_table("Sparse attention", first.payload["paper"]["abstract"]),
        f"Kernel fusion\n\n{second.payload['paper']['abstract']}": DOC,
        "kernel fusion": [0.85, 0.0, 0.1],
    }
    embedder = CountingEmbedder(table)
    out = _categorizer(embedder, FakeLLM("unused"), fallback=False).run_batch(
        [first, signal, second]
    )

    assert embedder.calls == 2  # one merged doc call, one merged candidate call
    assert [item.envelope.kind for item in out] == ["paper", "signal", "paper"]
    assert first.payload["enrichment"]["keywords"] == ["sparse attention", "transformer inference"]
    assert "kernel fusion" in second.payload["enrichment"]["keywords"]
    assert out[0].vector == DOC and out[1].vector is None and out[2].vector == DOC
    assert first.lineage[-1].outcome == "ok" and second.lineage[-1].outcome == "ok"


def test_run_batch_degrades_to_serial_on_batch_embed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(categorize_mod, "list_topics", lambda session: [])
    envelope = _paper_envelope()
    table = _table("Sparse attention", envelope.payload["paper"]["abstract"])
    out = _categorizer(FlakyEmbedder(table), FakeLLM("unused"), fallback=False).run_batch(
        [envelope]
    )

    assert envelope.payload["enrichment"]["keywords"] == [
        "sparse attention",
        "transformer inference",
    ]
    assert out[0].vector == DOC
    assert envelope.lineage[-1].outcome == "ok"


def test_keyword_embedder_scores_in_its_own_space(monkeypatch: pytest.MonkeyPatch) -> None:
    # The doc embedder maps the text along x (the stored vector); the keyword embedder
    # maps it along y. "sparse attention" only scores in the keyword space - if scoring
    # wrongly used the doc vector, its similarity would fall under the floor.
    monkeypatch.setattr(categorize_mod, "list_topics", lambda session: [])
    envelope = _paper_envelope()
    text = f"Sparse attention\n\n{envelope.payload['paper']['abstract']}"
    doc_embedder = FakeEmbedder({text: DOC})
    keyword_embedder = FakeEmbedder({text: [0.0, 1.0, 0.0], "sparse attention": [0.1, 0.9, 0.0]})
    categorizer = Categorizer(
        doc_embedder,
        FakeLLM("unused"),
        _no_session,
        topic_match_min=0.55,
        keyword_min_similarity=0.35,
        keywords_llm_fallback=False,
        labels=[],
        keyword_embedder=keyword_embedder,
    )

    result = categorizer.run_batch([envelope])[0]

    enrichment = envelope.payload["enrichment"]
    assert enrichment["keywords"] == ["sparse attention"]
    assert enrichment["keyword_method"] == "static"
    assert result.vector == DOC  # the stored embedding stays in the doc space


def test_non_paper_and_failed_parse_pass_through() -> None:
    signal = Envelope(
        kind="signal", source="s2", fetched_at=datetime(2026, 7, 30, tzinfo=UTC), payload={}
    )
    result = _categorizer(FakeEmbedder(), FakeLLM()).run(signal)
    assert result.vector is None and signal.lineage == []

    unparsed = Envelope(
        kind="paper", source="arxiv", fetched_at=datetime(2026, 7, 30, tzinfo=UTC), payload={}
    )
    _categorizer(FakeEmbedder(), FakeLLM()).run(unparsed)
    assert unparsed.lineage[-1].outcome == "skipped"


def test_centroid_cache_refreshes_on_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    loads = 0

    def counting_list_topics(session: object) -> list[SimpleNamespace]:
        nonlocal loads
        loads += 1
        return []

    monkeypatch.setattr(categorize_mod, "list_topics", counting_list_topics)
    clock_now = 0.0
    categorizer = Categorizer(
        FakeEmbedder(),
        FakeLLM(),
        _no_session,
        topic_match_min=0.55,
        keyword_min_similarity=0.35,
        keywords_llm_fallback=False,
        labels=[],
        clock=lambda: clock_now,
    )
    categorizer.run(_paper_envelope())
    categorizer.run(_paper_envelope())
    assert loads == 1  # second run inside the refresh window
    clock_now = 1000.0
    categorizer.run(_paper_envelope())
    assert loads == 2


def test_load_labels_reads_the_config(tmp_path: Path) -> None:
    assert load_labels(tmp_path / "missing.yaml") == []
    config = tmp_path / "labels.yaml"
    config.write_text("labels:\n  - name: safety\n    description: Alignment work.\n  - name: ''\n")
    assert load_labels(config) == [LabelSpec("safety", "Alignment work.")]
