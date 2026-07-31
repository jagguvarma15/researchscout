"""Broker plumbing against a real Kafka API (Redpanda container, integration only)."""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from researchscout.stream.broker import KafkaBroker, StreamTopics, ensure_topics
from researchscout.stream.envelope import Envelope, decode, encode

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def bootstrap() -> Iterator[str]:
    from testcontainers.kafka import RedpandaContainer

    with RedpandaContainer() as redpanda:
        yield redpanda.get_bootstrap_server()


def test_topics_publish_and_consume_round_trip(bootstrap: str) -> None:
    from confluent_kafka import Consumer

    topics = StreamTopics.for_prefix("rs")
    ensure_topics(bootstrap, topics)
    ensure_topics(bootstrap, topics)  # idempotent second startup

    envelope = Envelope(
        kind="paper",
        source="arxiv",
        fetched_at=datetime(2026, 7, 30, 6, tzinfo=UTC),
        payload={"paper": {"id": "arxiv:2607.1", "title": "T"}},
    )
    broker = KafkaBroker(bootstrap)
    broker.publish(topics.raw, envelope.key(), encode(envelope))
    broker.flush()

    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": "rs-test",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([topics.raw])
    message = None
    for _ in range(30):
        message = consumer.poll(1.0)
        if message is not None and not message.error():
            break
    consumer.close()

    assert message is not None and not message.error()
    assert message.key() == b"arxiv:2607.1"
    received = decode(message.value())
    assert received.event_id == envelope.event_id
    assert received.payload["paper"]["title"] == "T"
