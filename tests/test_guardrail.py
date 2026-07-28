from researchscout.guardrail import is_research_question
from researchscout.llm.base import LLM


class CannedLLM(LLM):
    model = "fake"

    def __init__(self, reply: str) -> None:
        self._reply = reply

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
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
