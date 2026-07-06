"""The EventSink seam: the ingest pipeline reports domain events without knowing about Kafka.

``NullSink`` keeps the CLI pull path and every existing test byte-for-byte unchanged;
``KafkaEventSink`` is what the workers plug in. Delivery is at-least-once — consumers rely on
the store's idempotent upserts, not on exactly-once plumbing.
"""

from __future__ import annotations

from typing import Any, Protocol

from researchscout.events.schemas import TOPIC_PAPERS_NEW, PaperCreated
from researchscout.schema import Paper


class EventSink(Protocol):
    def paper_created(self, paper: Paper) -> None: ...

    def flush(self) -> None: ...


class NullSink:
    """The default sink: events go nowhere."""

    def paper_created(self, paper: Paper) -> None:
        return None

    def flush(self) -> None:
        return None


class KafkaEventSink:
    """Produce domain events to their topics, keyed for future partitioning."""

    def __init__(self, producer: Any | None = None) -> None:
        if producer is None:
            from researchscout.events.kafka import producer as make_producer

            producer = make_producer()
        self._producer = producer

    def paper_created(self, paper: Paper) -> None:
        event = PaperCreated(paper=paper)
        self._producer.produce(
            TOPIC_PAPERS_NEW,
            key=paper.id.encode(),
            value=event.model_dump_json().encode(),
        )

    def flush(self) -> None:
        self._producer.flush()
