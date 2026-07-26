import logging
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


def test_answer_cites_only_retrieved(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored("arxiv:2401.00001"), _scored("arxiv:2401.00002")]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)
    llm = FakeLLM("Great [arxiv:2401.00001] but also invented [arxiv:9999.99999].")
    result = answer(None, None, llm, "q")
    assert result.cited == ["arxiv:2401.00001"]
    assert result.hallucinated == ["arxiv:9999.99999"]


def test_prompt_contains_retrieved_context(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored("arxiv:2401.00001", title="Cool Paper", abstract="An abstract here.")]
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: used)
    llm = FakeLLM("[arxiv:2401.00001]")
    answer(None, None, llm, "what is cool?")
    assert "[arxiv:2401.00001]" in llm.last_user
    assert "Cool Paper" in llm.last_user
    assert "An abstract here." in llm.last_user


def test_answer_empty_when_no_papers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(answer_mod, "retrieve", lambda *a, **k: [])
    result = answer(None, None, FakeLLM("unused"), "q")
    assert result.used == []
    assert result.cited == []


def test_trace_span_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="researchscout.trace"):
        with trace_span("unit", foo=1) as span:
            span["bar"] = 2
    assert any("unit" in record.getMessage() for record in caplog.records)
