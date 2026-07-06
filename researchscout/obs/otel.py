"""OpenTelemetry bootstrap for service entrypoints (the API and the workers).

Everything is gated on ``RS_OTEL_ENABLED`` and imported lazily, so the core library and CLI
never pay for the SDK. Traces, metrics, and logs all flow OTLP/gRPC to one collector; where
they land (Tempo/Prometheus/Loki locally, Dynatrace during a trial) is collector config,
not code.
"""

from __future__ import annotations

import logging

from researchscout.config import get_settings


def init_otel(service_name: str) -> None:
    """Install tracer/meter/logger providers and library instrumentation, if enabled."""
    settings = get_settings()
    if not settings.otel_enabled:
        return

    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    endpoint = settings.otlp_endpoint
    resource = Resource.create({"service.name": service_name})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
    )
    trace.set_tracer_provider(tracer_provider)

    reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint, insecure=True))
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint, insecure=True))
    )
    logging.getLogger().addHandler(LoggingHandler(logger_provider=logger_provider))

    HTTPXClientInstrumentor().instrument()
    try:
        from opentelemetry.instrumentation.confluent_kafka import ConfluentKafkaInstrumentor

        ConfluentKafkaInstrumentor().instrument()
    except ImportError:
        pass  # the api image has no kafka extra; workers do


def instrument_app(app: object) -> None:
    """Attach FastAPI request instrumentation, if enabled."""
    if not get_settings().otel_enabled:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]


def instrument_engine(engine: object) -> None:
    """Attach SQL query spans to one engine, if enabled.

    Called at engine creation (store.db) rather than globally: the store imports
    ``create_engine`` by reference at module load, so patching sqlalchemy after the fact
    would only ever catch the pool's connect events, not the queries.
    """
    if not get_settings().otel_enabled:
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=engine)
