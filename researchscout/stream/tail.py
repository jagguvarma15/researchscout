"""Console consumer behind ``scout stream tail``: watch packets flow through a topic.

Uses a throwaway consumer group per invocation with commits disabled, so tailing never
disturbs the worker's offsets. Formatting is a pure function for tests; the consume loop
yields formatted lines until interrupted.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from researchscout.stream.envelope import decode


def format_packet(value: bytes) -> str:
    """One compact human line per packet; undecodable bytes degrade gracefully."""
    try:
        envelope = decode(value)
    except ValueError:
        return f"<undecodable {len(value)} bytes>"
    stages = " ".join(
        f"{stamp.stage}:{stamp.outcome}"
        + (f"({stamp.error})" if stamp.outcome != "ok" and stamp.error else "")
        for stamp in envelope.lineage
    )
    paper = envelope.payload.get("paper") or {}
    title = paper.get("title") or envelope.payload.get("paper_id") or ""
    parts = [envelope.event_id[:8], envelope.kind, envelope.source]
    if stages:
        parts.append(stages)
    if title:
        parts.append(str(title)[:80])
    return "  ".join(parts)


def iter_lines(
    topic: str, *, bootstrap: str, from_beginning: bool = False, poll_timeout: float = 1.0
) -> Iterator[str]:
    """Yield formatted packet lines from a topic until the caller stops iterating."""
    from confluent_kafka import Consumer

    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": f"rs-tail-{os.getpid()}",
            "auto.offset.reset": "earliest" if from_beginning else "latest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([topic])
    try:
        while True:
            message = consumer.poll(poll_timeout)
            if message is None or message.error():
                continue
            value = message.value()
            if value is not None:
                yield format_packet(value)
    finally:
        consumer.close()
