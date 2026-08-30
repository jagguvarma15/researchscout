import contextlib

import pytest

from researchscout.guardrail import is_research_question
from researchscout.llm.base import LLM
from researchscout.llm.usage import current_purpose


class CannedLLM(LLM):
    model = "fake"

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.purpose_seen: str | None = None

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        self.purpose_seen = current_purpose()
        return self._reply


class BrokenLLM(LLM):
    model = "fake"

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        raise RuntimeError("model unavailable")


def test_clean_yes_is_in_scope() -> None:
    assert is_research_question(CannedLLM("yes"), "q") is True


def test_decorated_yes_is_in_scope() -> None:
    assert is_research_question(CannedLLM("Yes."), "q") is True


def test_clean_no_refuses() -> None:
    assert is_research_question(CannedLLM("no"), "q") is False


def test_no_with_trailing_prose_refuses() -> None:
    assert is_research_question(CannedLLM("No, it is not."), "q") is False


def test_empty_reply_fails_open() -> None:
    assert is_research_question(CannedLLM(""), "q") is True


def test_unexpected_reply_fails_open() -> None:
    assert is_research_question(CannedLLM("maybe so"), "q") is True


def test_llm_error_fails_open() -> None:
    assert is_research_question(BrokenLLM(), "q") is True


def test_the_call_is_tagged_with_the_guardrail_purpose() -> None:
    llm = CannedLLM("yes")
    is_research_question(llm, "q")
    assert llm.purpose_seen == "guardrail"


def test_a_pipeline_run_step_carries_the_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    from researchscout.llm.tracing import PipelineRun

    run_helpers = pytest.importorskip("langsmith.run_helpers")
    monkeypatch.setattr(run_helpers, "tracing_context", lambda *, parent: contextlib.nullcontext())

    class _FakeChild:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.ended: dict[str, object] | None = None

        def create_child(self, **kwargs: object) -> "_FakeChild":
            self.kwargs["child"] = _FakeChild(**kwargs)
            return self.kwargs["child"]  # type: ignore[return-value]

        def post(self) -> None:
            return None

        def patch(self) -> None:
            return None

        def end(self, *, outputs: object = None, error: object = None) -> None:
            self.ended = {"outputs": outputs, "error": error}

    root = _FakeChild()
    assert is_research_question(CannedLLM("no"), "q", run=PipelineRun(root)) is False
    child = root.kwargs["child"]
    assert isinstance(child, _FakeChild)
    assert child.kwargs["name"] == "guardrail"
    assert child.ended == {"outputs": {"verdict": "no"}, "error": None}
