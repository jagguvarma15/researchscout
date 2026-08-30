"""The OpenAI-compat client: LangSmith wrapping, attribution headers, usage recording."""

from types import SimpleNamespace
from typing import Any

import pytest

from researchscout.llm.openai_compat import OpenAICompatLLM, _maybe_trace
from researchscout.llm.usage import LlmCallUsage, llm_purpose


def test_the_bare_client_is_untouched_without_the_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    client = object()
    assert _maybe_trace(client) is client
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    assert _maybe_trace(client) is client


def test_tracing_wraps_the_client_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    wrappers = pytest.importorskip("langsmith.wrappers")
    wrapped = object()
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr(wrappers, "wrap_openai", lambda client: wrapped)
    assert _maybe_trace(object()) is wrapped


def test_the_client_carries_the_attribution_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenRouter reads these to attribute traffic; every other provider ignores them."""
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    llm = OpenAICompatLLM(base_url="http://localhost:9/v1", model="m", api_key="k")
    assert llm._client.default_headers.get("X-Title") == "ResearchScout"
    assert "HTTP-Referer" in llm._client.default_headers


def test_the_client_honors_the_timeout_and_retry_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.setenv("RS_LLM_TIMEOUT_SEC", "7.5")
    monkeypatch.setenv("RS_LLM_MAX_RETRIES", "0")
    llm = OpenAICompatLLM(base_url="http://localhost:9/v1", model="m", api_key="k")
    assert llm._client.timeout == 7.5
    assert llm._client.max_retries == 0


def test_the_client_retries_once_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The openai default of 2 turns every daily-cap 429 into three spent requests."""
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("RS_LLM_MAX_RETRIES", raising=False)
    llm = OpenAICompatLLM(base_url="http://localhost:9/v1", model="m", api_key="k")
    assert llm._client.max_retries == 1


# --- usage recording ---


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses: list[Any] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _fake_client() -> tuple[Any, _FakeCompletions]:
    completions = _FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def _instrumented(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[OpenAICompatLLM, _FakeCompletions, list[LlmCallUsage]]:
    recorded: list[LlmCallUsage] = []
    monkeypatch.setattr("researchscout.llm.openai_compat.record_usage", recorded.append)
    llm = OpenAICompatLLM(base_url="http://localhost:9/v1", model="m", api_key="k")
    client, completions = _fake_client()
    llm.__dict__["_client"] = client
    return llm, completions, recorded


def _reply(text: str, *, prompt: int | None = 100, completion: int | None = 20) -> Any:
    usage = None
    if prompt is not None:
        usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)
    message = SimpleNamespace(content=text)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def _chunk(text: str | None, *, usage: Any = None) -> Any:
    choices = []
    if text is not None:
        choices = [SimpleNamespace(delta=SimpleNamespace(content=text))]
    return SimpleNamespace(choices=choices, usage=usage)


def test_complete_records_tokens_and_the_purpose(monkeypatch: pytest.MonkeyPatch) -> None:
    llm, completions, recorded = _instrumented(monkeypatch)
    completions.responses = [_reply("hello")]
    with llm_purpose("digest"):
        assert llm.complete("s", "u") == "hello"
    assert len(recorded) == 1
    call = recorded[0]
    assert call.purpose == "digest" and call.model == "m"
    assert call.prompt_tokens == 100 and call.completion_tokens == 20
    assert call.outcome == "ok" and call.detail is None


def test_complete_without_usage_records_null_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    llm, completions, recorded = _instrumented(monkeypatch)
    completions.responses = [_reply("hello", prompt=None)]
    llm.complete("s", "u")
    assert recorded[0].prompt_tokens is None and recorded[0].completion_tokens is None


def test_a_quota_failure_records_and_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    llm, completions, recorded = _instrumented(monkeypatch)

    class _Quota(Exception):
        status_code = 429

    completions.responses = [_Quota("over the daily cap")]
    with pytest.raises(_Quota):
        llm.complete("s", "u")
    assert recorded[0].outcome == "quota"
    assert recorded[0].detail is not None


def test_a_plain_failure_records_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    llm, completions, recorded = _instrumented(monkeypatch)
    completions.responses = [RuntimeError("connection refused")]
    with pytest.raises(RuntimeError):
        llm.complete("s", "u")
    assert recorded[0].outcome == "error"


def test_stream_requests_usage_and_records_on_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm, completions, recorded = _instrumented(monkeypatch)
    final = _chunk(None, usage=SimpleNamespace(prompt_tokens=50, completion_tokens=7))
    completions.responses = [iter([_chunk("a"), _chunk("b"), final])]
    with llm_purpose("synthesis"):
        deltas = llm.stream("s", "u")
    # Eager by design: the request went out (and the purpose was read) at call time.
    assert completions.calls[0]["stream_options"] == {"include_usage": True}
    assert recorded == []
    assert list(deltas) == ["a", "b"]
    assert len(recorded) == 1
    call = recorded[0]
    assert call.purpose == "synthesis"
    assert call.prompt_tokens == 50 and call.completion_tokens == 7
    assert call.outcome == "ok"


def test_stream_usage_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RS_LLM_STREAM_USAGE", "false")
    llm, completions, recorded = _instrumented(monkeypatch)
    completions.responses = [iter([_chunk("a")])]
    list(llm.stream("s", "u"))
    assert "stream_options" not in completions.calls[0]
    assert recorded[0].prompt_tokens is None


def test_a_rejected_stream_options_retries_once_and_latches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openai import BadRequestError

    llm, completions, recorded = _instrumented(monkeypatch)
    rejected = BadRequestError.__new__(BadRequestError)
    completions.responses = [rejected, iter([_chunk("a")]), iter([_chunk("b")])]
    assert list(llm.stream("s", "u")) == ["a"]
    # First call carried the parameter, the retry dropped it, the latch keeps it off.
    assert "stream_options" in completions.calls[0]
    assert "stream_options" not in completions.calls[1]
    assert list(llm.stream("s", "u")) == ["b"]
    assert "stream_options" not in completions.calls[2]


def test_a_failing_stream_records_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    llm, completions, recorded = _instrumented(monkeypatch)

    def chunks() -> Any:
        yield _chunk("a")
        raise RuntimeError("connection dropped")

    completions.responses = [chunks()]
    deltas = llm.stream("s", "u")
    with pytest.raises(RuntimeError):
        list(deltas)
    assert recorded[0].outcome == "error"
    assert recorded[0].detail is not None


def test_an_abandoned_stream_records_aborted(monkeypatch: pytest.MonkeyPatch) -> None:
    llm, completions, recorded = _instrumented(monkeypatch)
    completions.responses = [iter([_chunk("a"), _chunk("b")])]
    deltas = llm.stream("s", "u")
    assert next(deltas) == "a"
    deltas.close()
    assert recorded[0].outcome == "aborted"
