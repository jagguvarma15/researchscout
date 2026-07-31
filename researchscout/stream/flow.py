"""The Bytewax dataflow: raw packets through parse, categorize, and inject, with taps.

This file (plus serve.py) is deliberately the only place bytewax is imported; the stage
functions it wires are plain callables, so swapping the processor for a hand-rolled
consume loop stays a two-file change. One fused in-process flow keeps a single bge model
in RAM, and the Kafka taps after parse and inject are what scout stream tail watches.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import bytewax.operators as op
from bytewax.connectors.kafka import (
    KafkaError,
    KafkaSink,
    KafkaSinkMessage,
    KafkaSource,
    KafkaSourceMessage,
)
from bytewax.dataflow import Dataflow

from researchscout.stream.broker import PRODUCER_TUNING, StreamTopics
from researchscout.stream.categorize import Categorized
from researchscout.stream.envelope import Envelope, decode, encode

logger = logging.getLogger(__name__)

# The tap sinks flush once per write batch; the same linger/compression tuning as the
# producers keeps those flushes cheap.
_SINK_TUNING = dict(PRODUCER_TUNING)


@dataclass(frozen=True)
class FlowDeps:
    """The stage callables, injected so tests wire fakes into the same graph.

    Parse stays per-item (its self-time is ~0); categorize and inject take whole batches
    so merged embeds and one-transaction sinks amortize across the consumer batch.
    """

    parse: Callable[[Envelope], Envelope]
    categorize_batch: Callable[[list[Envelope]], list[Categorized]]
    inject_batch: Callable[[list[Categorized]], list[Envelope]]


def decode_message(
    message: KafkaSourceMessage[bytes | None, bytes | None]
    | KafkaError[bytes | None, bytes | None],
) -> Envelope | None:
    """Bytes to envelope; malformed or future-versioned packets are logged and dropped."""
    if isinstance(message, KafkaError):  # raise_on_errors=True means these never arrive
        return None
    if message.value is None:
        return None
    try:
        return decode(message.value)
    except ValueError:
        logger.warning("dropping undecodable packet", exc_info=True)
        return None


def to_sink_message(envelope: Envelope) -> KafkaSinkMessage[bytes, bytes]:
    """Serialize an envelope for a tap topic, keyed like the raw topic."""
    return KafkaSinkMessage(key=envelope.key().encode("utf-8"), value=encode(envelope))


def build_flow(
    bootstrap: str,
    topics: StreamTopics,
    consumer_group: str,
    deps: FlowDeps,
    *,
    batch_size: int = 100,
    fulltext_batch_size: int = 1,
    taps: bool = True,
) -> Dataflow:
    """Wire the production graph: Kafka in, three stages, two observability taps.

    Fulltext packets arrive on their own input with a batch size of one (the slow lane):
    bytewax polls every input each cycle, so at most one chunk-heavy fulltext interleaves
    per batch of papers instead of queueing ahead of them. Sources carry their resume
    state per step id, so adding the second input never disturbs raw-in's offsets.
    ``taps=False`` drops the parsed/enriched tap topics (and their per-batch producer
    flushes); scout stream tail goes dark in that mode.
    """
    flow = Dataflow("researchscout-stream")
    raw_messages = op.input(
        "raw-in",
        flow,
        KafkaSource(
            brokers=[bootstrap],
            topics=[topics.raw],
            add_config={"group.id": consumer_group},
            batch_size=batch_size,
        ),
    )
    fulltext_messages = op.input(
        "fulltext-in",
        flow,
        KafkaSource(
            brokers=[bootstrap],
            topics=[topics.raw_fulltext],
            add_config={"group.id": consumer_group},
            batch_size=fulltext_batch_size,
        ),
    )
    messages = op.merge("merge-raw", raw_messages, fulltext_messages)
    envelopes = op.filter_map("decode", messages, decode_message)
    parsed = op.map("parse", envelopes, deps.parse)
    if taps:
        parsed_tap = op.map("parsed-msg", parsed, to_sink_message)
        op.output(
            "parsed-tap",
            parsed_tap,
            KafkaSink(brokers=[bootstrap], topic=topics.parsed, add_config=_SINK_TUNING),
        )
    categorized = op.flat_map_batch("categorize", parsed, deps.categorize_batch)
    injected = op.flat_map_batch("inject", categorized, deps.inject_batch)
    if taps:
        enriched_tap = op.map("enriched-msg", injected, to_sink_message)
        op.output(
            "enriched-tap",
            enriched_tap,
            KafkaSink(brokers=[bootstrap], topic=topics.enriched, add_config=_SINK_TUNING),
        )
    return flow
