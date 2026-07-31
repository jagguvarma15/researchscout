from datetime import UTC, datetime

from researchscout.stream.envelope import Envelope, encode
from researchscout.stream.tail import format_packet


def _envelope() -> Envelope:
    envelope = Envelope(
        event_id="abcdef1234567890",
        kind="paper",
        source="arxiv",
        fetched_at=datetime(2026, 7, 30, tzinfo=UTC),
        payload={"paper": {"id": "arxiv:2607.1", "title": "A Great Paper"}},
    )
    for stage in ("produce", "parse"):
        stamp = envelope.begin(stage)  # type: ignore[arg-type]
        envelope.finish(stamp)
    failed = envelope.begin("inject")
    envelope.finish(failed, "error", "db unavailable")
    return envelope


def test_format_packet_compact_line() -> None:
    line = format_packet(encode(_envelope()))
    assert line.startswith("abcdef12  paper  arxiv")
    assert "produce:ok parse:ok inject:error(db unavailable)" in line
    assert line.endswith("A Great Paper")


def test_format_packet_degrades_on_junk() -> None:
    assert format_packet(b"junk") == "<undecodable 4 bytes>"
