"""Schema-versioned packet envelope with per-stage lineage stamps.

Every record flowing through the pipeline is one Envelope serialized as JSON. The lineage
list is stamped by each stage (produce, parse, categorize, inject), so a packet carries its
own processing history into the observability taps and the lineage store.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

ENVELOPE_VERSION = 1

Kind = Literal["paper", "signal", "fulltext"]
Stage = Literal["produce", "parse", "categorize", "inject"]
Outcome = Literal["ok", "error", "skipped"]


class LineageStamp(BaseModel):
    stage: Stage
    entered_at: datetime
    exited_at: datetime | None = None
    outcome: Outcome = "ok"
    error: str | None = None


class Envelope(BaseModel):
    v: int = ENVELOPE_VERSION
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    kind: Kind
    source: str
    fetched_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    lineage: list[LineageStamp] = Field(default_factory=list)

    def begin(self, stage: Stage) -> LineageStamp:
        """Open a lineage stamp for a stage; close it with :meth:`finish`."""
        stamp = LineageStamp(stage=stage, entered_at=datetime.now(UTC))
        self.lineage.append(stamp)
        return stamp

    def finish(
        self, stamp: LineageStamp, outcome: Outcome = "ok", error: str | None = None
    ) -> None:
        """Close a lineage stamp with its outcome."""
        stamp.exited_at = datetime.now(UTC)
        stamp.outcome = outcome
        stamp.error = error

    def key(self) -> str:
        """The Kafka message key: the canonical paper id when known, else the event id."""
        for path in (("paper", "id"), ("signal", "paper_id"), ("paper_id",)):
            value: Any = self.payload
            for part in path:
                value = value.get(part) if isinstance(value, dict) else None
            if isinstance(value, str) and value:
                return value
        return self.event_id


def encode(envelope: Envelope) -> bytes:
    """Serialize an envelope for a Kafka message value."""
    return envelope.model_dump_json().encode("utf-8")


def decode(data: bytes) -> Envelope:
    """Parse a message value; raises ValueError on malformed JSON or a version mismatch."""
    try:
        envelope = Envelope.model_validate_json(data)
    except ValidationError as exc:
        raise ValueError(f"malformed envelope: {exc}") from exc
    if envelope.v != ENVELOPE_VERSION:
        raise ValueError(f"unsupported envelope version {envelope.v}")
    return envelope
