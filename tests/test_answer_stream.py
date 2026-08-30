from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

import researchscout.answer as answer_mod
from researchscout.answer import Answer, StreamDelta, StreamMeta, answer_stream
from researchscout.llm.base import LLM
from researchscout.retrieve.search import ScoredPaper
from researchscout.schema import Author, Paper


class ChunkedLLM(LLM):
    model = "fake"

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        return "".join(self._chunks)

    def stream(self, system: str, user: str, *, temperature: float = 0.2) -> Iterator[str]:
        yield from self._chunks


class CompleteOnlyLLM(LLM):
    """Never overrides stream() — exercises the ABC's one-chunk default."""

    model = "fake"

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        return "all at once [arxiv:2401.00001]"


def _scored(pid: str) -> ScoredPaper:
    paper = Paper(
        id=pid,
        title="T",
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source="arxiv",
    )
    return ScoredPaper(paper=paper, score=1.0, distance=0.0)


def test_stream_yields_meta_deltas_then_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored("arxiv:2401.00001")]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)
    llm = ChunkedLLM(["Good ", "[arxiv:2401.00001]", " but fake [arxiv:9999.99999]."])

    events = list(answer_stream(None, _StubEmbedder(), llm, "q"))

    meta = events[0]
    assert isinstance(meta, StreamMeta) and meta.retrieved == 1
    deltas = [e for e in events if isinstance(e, StreamDelta)]
    assert "".join(d.text for d in deltas) == "Good [arxiv:2401.00001] but fake [arxiv:9999.99999]."
    final = events[-1]
    assert isinstance(final, Answer)
    assert final.cited == ["arxiv:2401.00001"]
    assert final.hallucinated == ["arxiv:9999.99999"]


@pytest.fixture(autouse=True)
def _hermetic_chunk_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic against a local .env that enables chunk retrieval (tests opt in explicitly)."""
    monkeypatch.setenv("RS_CHUNK_RETRIEVAL", "0")


def test_stream_citation_split_across_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored("arxiv:2401.00001")]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)
    llm = ChunkedLLM(["See [arxiv:24", "01.00001] here."])

    final = list(answer_stream(None, _StubEmbedder(), llm, "q"))[-1]
    assert isinstance(final, Answer)
    assert final.cited == ["arxiv:2401.00001"]


def test_stream_empty_retrieval_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: [])
    events = list(answer_stream(None, _StubEmbedder(), ChunkedLLM(["unused"]), "q"))
    assert isinstance(events[0], StreamMeta) and events[0].retrieved == 0
    # The no-results text still arrives as a delta, so clients render uniformly from tokens.
    assert isinstance(events[1], StreamDelta)
    assert isinstance(events[-1], Answer) and events[-1].used == []
    assert len(events) == 3


class _CapturingLLM(LLM):
    model = "fake"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        self.prompts.append(user)
        return "ok [arxiv:2401.00001]"


class _StubEmbedder:
    model_id = "stub"
    dim = 3

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0]


def test_stream_quotes_capped_excerpts_when_chunks_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    used = [_scored("arxiv:2401.00001")]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)
    monkeypatch.setenv("RS_CHUNK_RETRIEVAL", "1")
    monkeypatch.setattr(
        answer_mod,
        "best_chunk_texts",
        lambda *a, **k: {"arxiv:2401.00001": "sinkhorn iterations converge " * 60},
    )
    llm = _CapturingLLM()
    list(answer_stream(None, _StubEmbedder(), llm, "q"))  # type: ignore[arg-type]
    prompt = llm.prompts[0]
    assert "Excerpt: sinkhorn iterations converge" in prompt
    excerpt = prompt.split("Excerpt: ", 1)[1].split("\n\n", 1)[0]
    assert len(excerpt) <= 600  # capped to stay inside the model's context window


def test_stream_skips_excerpts_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored("arxiv:2401.00001")]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)
    monkeypatch.setenv("RS_CHUNK_RETRIEVAL", "0")

    def boom(*a: object, **k: object) -> dict:
        raise AssertionError("best_chunk_texts must not be called when the flag is off")

    monkeypatch.setattr(answer_mod, "best_chunk_texts", boom)
    llm = _CapturingLLM()
    list(answer_stream(None, _StubEmbedder(), llm, "q"))
    assert "Excerpt:" not in llm.prompts[0]


def test_default_stream_is_single_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored("arxiv:2401.00001")]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)
    events = list(answer_stream(None, _StubEmbedder(), CompleteOnlyLLM(), "q"))
    deltas = [e for e in events if isinstance(e, StreamDelta)]
    assert len(deltas) == 1
    final = events[-1]
    assert isinstance(final, Answer) and final.cited == ["arxiv:2401.00001"]


def test_an_agentic_stream_yields_the_plan_first(monkeypatch: pytest.MonkeyPatch) -> None:
    import researchscout.agentic as agentic_mod
    from researchscout.answer import StreamPlan

    used = [_scored("arxiv:2401.00001")]
    monkeypatch.setattr(agentic_mod, "decompose", lambda llm, q: ["alpha", "beta"])
    monkeypatch.setattr(agentic_mod, "agentic_retrieve", lambda *a, **k: used)
    llm = ChunkedLLM(["ok [arxiv:2401.00001]"])

    events = list(answer_stream(None, _StubEmbedder(), llm, "q", agentic=True))

    assert isinstance(events[0], StreamPlan) and events[0].parts == ["alpha", "beta"]
    assert isinstance(events[1], StreamMeta)
    final = events[-1]
    assert isinstance(final, Answer)
    assert final.plan == ["alpha", "beta"]
    assert final.model == "fake"


def test_an_atomic_deep_ask_streams_no_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    import researchscout.agentic as agentic_mod
    from researchscout.answer import StreamPlan

    used = [_scored("arxiv:2401.00001")]
    monkeypatch.setattr(agentic_mod, "decompose", lambda llm, q: [q])
    monkeypatch.setattr(agentic_mod, "agentic_retrieve", lambda *a, **k: used)

    events = list(answer_stream(None, _StubEmbedder(), ChunkedLLM(["ok"]), "q", agentic=True))
    assert not any(isinstance(e, StreamPlan) for e in events)
    final = events[-1]
    assert isinstance(final, Answer) and final.plan is None


def test_the_stream_fills_the_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored("arxiv:2401.00001")]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)
    timings: dict[str, float] = {}
    list(answer_stream(None, _StubEmbedder(), ChunkedLLM(["ok"]), "q", timings=timings))
    assert "retrieve_ms" in timings and timings["retrieve_ms"] >= 0.0
    assert "llm_ms" in timings and timings["llm_ms"] >= 0.0


class _RecordingLLM(ChunkedLLM):
    """Mimics the real client: eager stream, usage recorded at call time."""

    def stream(self, system: str, user: str, *, temperature: float = 0.2) -> Iterator[str]:
        from researchscout.llm.usage import LlmCallUsage, current_purpose, record_usage

        record_usage(
            LlmCallUsage(
                purpose=current_purpose(),
                model=self.model,
                prompt_tokens=111,
                completion_tokens=22,
                latency_ms=5,
                outcome="ok",
                detail=None,
            )
        )
        return iter(self._chunks)


def test_the_answer_carries_its_token_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored("arxiv:2401.00001")]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)

    def no_db() -> object:
        raise RuntimeError("no database in unit tests")

    # Keep the recorder away from the database; the contextvar copy is what matters here.
    monkeypatch.setattr("researchscout.store.db.session_scope", no_db)

    final = list(answer_stream(None, _StubEmbedder(), _RecordingLLM(["ok"]), "q"))[-1]
    assert isinstance(final, Answer)
    assert final.prompt_tokens == 111 and final.completion_tokens == 22
    assert final.model == "fake"


def test_a_non_recording_fake_leaves_tokens_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored("arxiv:2401.00001")]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)
    final = list(answer_stream(None, _StubEmbedder(), ChunkedLLM(["ok"]), "q"))[-1]
    assert isinstance(final, Answer)
    assert final.prompt_tokens is None and final.completion_tokens is None
