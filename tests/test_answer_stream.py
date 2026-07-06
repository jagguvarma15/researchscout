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

    events = list(answer_stream(None, None, llm, "q"))

    meta = events[0]
    assert isinstance(meta, StreamMeta) and meta.retrieved == 1
    deltas = [e for e in events if isinstance(e, StreamDelta)]
    assert "".join(d.text for d in deltas) == "Good [arxiv:2401.00001] but fake [arxiv:9999.99999]."
    final = events[-1]
    assert isinstance(final, Answer)
    assert final.cited == ["arxiv:2401.00001"]
    assert final.hallucinated == ["arxiv:9999.99999"]


def test_stream_citation_split_across_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored("arxiv:2401.00001")]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)
    llm = ChunkedLLM(["See [arxiv:24", "01.00001] here."])

    final = list(answer_stream(None, None, llm, "q"))[-1]
    assert isinstance(final, Answer)
    assert final.cited == ["arxiv:2401.00001"]


def test_stream_empty_retrieval_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: [])
    events = list(answer_stream(None, None, ChunkedLLM(["unused"]), "q"))
    assert isinstance(events[0], StreamMeta) and events[0].retrieved == 0
    # The no-results text still arrives as a delta, so clients render uniformly from tokens.
    assert isinstance(events[1], StreamDelta)
    assert isinstance(events[-1], Answer) and events[-1].used == []
    assert len(events) == 3


def test_default_stream_is_single_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored("arxiv:2401.00001")]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)
    events = list(answer_stream(None, None, CompleteOnlyLLM(), "q"))
    deltas = [e for e in events if isinstance(e, StreamDelta)]
    assert len(deltas) == 1
    final = events[-1]
    assert isinstance(final, Answer) and final.cited == ["arxiv:2401.00001"]
