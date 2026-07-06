import logging

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from researchscout.obs.trace import trace_span


def test_span_logs_without_any_backend(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="researchscout.trace"):
        with trace_span("unit", foo=1) as span:
            span["bar"] = 2
    assert any("unit" in record.getMessage() for record in caplog.records)


def test_span_exports_otel_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RS_OTEL_ENABLED", "true")
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # A tracer provider can only be set once per process; patch the lookup instead.
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)

    with trace_span("ask", question="q", k=8) as span:
        span["retrieved"] = 3

    (exported,) = exporter.get_finished_spans()
    assert exported.name == "ask"
    assert exported.attributes is not None
    assert exported.attributes["question"] == "q"
    assert exported.attributes["retrieved"] == 3
    assert "elapsed_ms" in exported.attributes


def test_langsmith_stays_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    # Would raise inside langsmith without an API key if the gate were broken.
    with trace_span("unit") as span:
        span["ok"] = True


def test_openai_client_not_wrapped_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    from researchscout.llm.openai_compat import OpenAICompatLLM

    client = OpenAICompatLLM(base_url="http://localhost:1", api_key="x")._client
    assert type(client).__module__.startswith("openai")
