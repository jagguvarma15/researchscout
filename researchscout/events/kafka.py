"""Thin confluent-kafka helpers: producer, manual-commit consumer, topic bootstrap."""

from __future__ import annotations

import logging
from typing import Any

from researchscout.config import get_settings

logger = logging.getLogger(__name__)


def producer() -> Any:
    from confluent_kafka import Producer

    return Producer({"bootstrap.servers": get_settings().kafka_bootstrap_servers})


def consumer(group: str, topics: list[str]) -> Any:
    """A consumer that commits manually — offsets advance only after the DB commit."""
    from confluent_kafka import Consumer

    client = Consumer(
        {
            "bootstrap.servers": get_settings().kafka_bootstrap_servers,
            "group.id": group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    client.subscribe(topics)
    return client


def ensure_topics(names: list[str], *, partitions: int = 1) -> None:
    """Create topics if missing (1 partition, RF 1 — single broker); existing ones are fine."""
    from confluent_kafka.admin import AdminClient, NewTopic  # type: ignore[attr-defined]

    admin = AdminClient({"bootstrap.servers": get_settings().kafka_bootstrap_servers})
    futures = admin.create_topics(
        [NewTopic(name, num_partitions=partitions, replication_factor=1) for name in names]
    )
    for name, future in futures.items():
        try:
            future.result()
            logger.info("created topic %s", name)
        except Exception as exc:  # noqa: BLE001 - TopicAlreadyExists arrives as KafkaException
            if "TOPIC_ALREADY_EXISTS" not in str(exc):
                raise
