"""The OpenAI-compat client: LangSmith wrapping and provider attribution headers."""

import pytest

from researchscout.llm.openai_compat import OpenAICompatLLM, _maybe_trace


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
