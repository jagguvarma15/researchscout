import json
from datetime import UTC, datetime

import pytest

from researchscout.stream.envelope import ENVELOPE_VERSION, Envelope, decode, encode


def _envelope(kind: str = "paper", payload: dict | None = None) -> Envelope:
    return Envelope(
        kind=kind,  # type: ignore[arg-type]
        source="arxiv",
        fetched_at=datetime(2026, 7, 30, 6, 0, tzinfo=UTC),
        payload=payload or {},
    )


def test_round_trip_preserves_fields_and_lineage() -> None:
    envelope = _envelope(payload={"raw": {"id": "2607.00001"}})
    stamp = envelope.begin("parse")
    envelope.finish(stamp, "ok")

    again = decode(encode(envelope))
    assert again.event_id == envelope.event_id
    assert again.kind == "paper"
    assert again.payload == {"raw": {"id": "2607.00001"}}
    assert [s.stage for s in again.lineage] == ["parse"]
    assert again.lineage[0].outcome == "ok"
    assert again.lineage[0].exited_at is not None
    assert again.lineage[0].entered_at <= again.lineage[0].exited_at


def test_decode_rejects_future_version() -> None:
    data = encode(_envelope())
    payload = json.loads(data)
    payload["v"] = ENVELOPE_VERSION + 1
    with pytest.raises(ValueError, match="unsupported envelope version"):
        decode(json.dumps(payload).encode())


def test_decode_rejects_malformed_json() -> None:
    with pytest.raises(ValueError, match="malformed envelope"):
        decode(b"not json at all")
    with pytest.raises(ValueError, match="malformed envelope"):
        decode(b'{"kind": "paper"}')  # missing required fields


def test_failed_stage_records_error() -> None:
    envelope = _envelope()
    stamp = envelope.begin("categorize")
    envelope.finish(stamp, "error", "llm timed out")
    assert stamp.outcome == "error"
    assert stamp.error == "llm timed out"


def test_key_prefers_canonical_ids() -> None:
    assert _envelope(payload={"paper": {"id": "arxiv:2607.1"}}).key() == "arxiv:2607.1"
    assert (
        _envelope("signal", payload={"signal": {"paper_id": "arxiv:2607.2"}}).key()
        == "arxiv:2607.2"
    )
    assert _envelope("fulltext", payload={"paper_id": "arxiv:2607.3"}).key() == "arxiv:2607.3"
    anonymous = _envelope(payload={"raw": {"anything": 1}})
    assert anonymous.key() == anonymous.event_id
