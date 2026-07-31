"""Thin broker seam over confluent-kafka plus an in-memory fake for tests.

The pipeline needs exactly three things from Kafka here: topic names, a producer, and
idempotent topic creation at startup. Consuming belongs to Bytewax's connectors and the
tail command. Keeping the seam this small means unit tests run on the fake and a broker
swap stays a one-file change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

_HOUR_MS = 3_600_000


@dataclass(frozen=True)
class StreamTopics:
    """The three pipeline topics for a prefix."""

    raw: str
    parsed: str
    enriched: str

    @classmethod
    def for_prefix(cls, prefix: str) -> StreamTopics:
        return cls(
            raw=f"{prefix}.raw.v1",
            parsed=f"{prefix}.parsed.v1",
            enriched=f"{prefix}.enriched.v1",
        )

    def configs(self) -> dict[str, dict[str, str]]:
        """Creation configs: 7d retention and 5MB messages on raw (fulltext packets), 3d taps."""
        return {
            self.raw: {"retention.ms": str(168 * _HOUR_MS), "max.message.bytes": "5242880"},
            self.parsed: {"retention.ms": str(72 * _HOUR_MS)},
            self.enriched: {"retention.ms": str(72 * _HOUR_MS)},
        }


# Producer batching: a short linger amortizes the per-message broker round-trips of a
# poll burst, and lz4 shrinks fulltext packets. Constants, not knobs - single-host values.
PRODUCER_TUNING = {"linger.ms": "50", "compression.type": "lz4"}


class Broker(Protocol):
    """What producers need: fire-and-forget publish plus a drain before shutdown."""

    def publish(self, topic: str, key: str, value: bytes) -> None: ...

    def flush(self, timeout: float | None = None) -> None: ...


class KafkaBroker:
    """confluent-kafka producer against the configured bootstrap servers."""

    def __init__(self, bootstrap: str) -> None:
        from confluent_kafka import Producer

        self._producer = Producer({"bootstrap.servers": bootstrap, **PRODUCER_TUNING})

    def publish(self, topic: str, key: str, value: bytes) -> None:
        self._producer.produce(topic, value=value, key=key)
        self._producer.poll(0)

    def flush(self, timeout: float | None = None) -> None:
        """Drain the queue; a timeout bounds the wait for best-effort callers."""
        if timeout is None:
            self._producer.flush()
        else:
            self._producer.flush(timeout)


@dataclass
class InMemoryBroker:
    """Test fake capturing published messages per topic."""

    messages: dict[str, list[tuple[str, bytes]]] = field(default_factory=dict)

    def publish(self, topic: str, key: str, value: bytes) -> None:
        self.messages.setdefault(topic, []).append((key, value))

    def flush(self, timeout: float | None = None) -> None:
        return None


def ensure_topics(bootstrap: str, topics: StreamTopics, *, partitions: int = 1) -> None:
    """Create the pipeline topics if missing (idempotent, safe on every startup)."""
    # NewTopic is not re-exported in confluent-kafka's admin stubs, hence the ignore.
    from confluent_kafka import KafkaError, KafkaException
    from confluent_kafka.admin import AdminClient, NewTopic  # type: ignore[attr-defined]

    admin = AdminClient({"bootstrap.servers": bootstrap})
    existing = set(admin.list_topics(timeout=10).topics)
    wanted = [
        NewTopic(name, num_partitions=partitions, replication_factor=1, config=config)
        for name, config in topics.configs().items()
        if name not in existing
    ]
    if not wanted:
        return
    for future in admin.create_topics(wanted).values():
        try:
            future.result(timeout=10)
        except KafkaException as exc:  # a concurrent startup already created it: fine
            if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
                raise
