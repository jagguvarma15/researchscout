from datetime import UTC, datetime
from pathlib import Path

import pytest

import researchscout.stream.parse as parse_mod
from researchscout.schema import Signal, SignalType
from researchscout.sources.arxiv import _entry_payload
from researchscout.stream.envelope import Envelope
from researchscout.stream.parse import (
    clean_text,
    looks_truncated,
    parse_stage,
    recover_abstract,
    strip_structural_tex,
)

FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_query.atom"


def _arxiv_raw() -> dict[str, object]:
    import feedparser

    feed = feedparser.parse(FIXTURE.read_text())
    return _entry_payload(feed.entries[0])


def _envelope(kind: str, source: str, payload: dict) -> Envelope:
    return Envelope(
        kind=kind,  # type: ignore[arg-type]
        source=source,
        fetched_at=datetime(2026, 7, 30, 6, 0, tzinfo=UTC),
        payload=payload,
    )


def test_paper_packet_normalizes_and_cleans() -> None:
    envelope = _envelope("paper", "arxiv", {"raw": _arxiv_raw()})
    parse_stage(envelope)

    assert [s.stage for s in envelope.lineage] == ["parse"]
    assert envelope.lineage[0].outcome == "ok"
    paper = envelope.payload["paper"]
    assert paper["id"] == "arxiv:2401.12345"
    assert paper["title"] == "A Great Paper on Transformers"
    assert envelope.payload["abstract_truncated"] is False
    assert envelope.key() == "arxiv:2401.12345"


def test_signal_packet_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    signal = Signal(
        paper_id="arxiv:2401.12345",
        type=SignalType.citation,
        source="semantic_scholar",
        value=12,
        observed_at=datetime(2026, 7, 30, tzinfo=UTC),
    )

    class StubSource:
        def normalize(self, raw: object) -> Signal:
            return signal

    monkeypatch.setattr(parse_mod, "get_source", lambda name: StubSource())
    envelope = _envelope("signal", "semantic_scholar", {"raw": {}})
    parse_stage(envelope)

    assert envelope.lineage[0].outcome == "ok"
    assert envelope.payload["signal"]["paper_id"] == "arxiv:2401.12345"
    assert envelope.payload["signal"]["value"] == 12.0
    assert envelope.key() == "arxiv:2401.12345"


def test_fulltext_packet_gains_section_headings() -> None:
    text = "Lead prose.\n\n## Introduction\n\nBody.\n\n## Method\n\nMore."
    envelope = _envelope("fulltext", "arxiv", {"paper_id": "arxiv:2401.1", "text": text})
    parse_stage(envelope)

    assert envelope.lineage[0].outcome == "ok"
    assert envelope.payload["sections"] == ["Introduction", "Method"]


def test_parse_failure_is_recorded_not_raised() -> None:
    envelope = _envelope("paper", "no-such-source", {"raw": {}})
    parse_stage(envelope)

    stamp = envelope.lineage[0]
    assert stamp.outcome == "error"
    assert stamp.error is not None and "no-such-source" in stamp.error
    assert "paper" not in envelope.payload  # nothing half-written


def test_strip_structural_tex_keeps_math() -> None:
    assert strip_structural_tex(r"a \emph{great} method") == "a great method"
    assert strip_structural_tex(r"{\it fast} and \textbf{\emph{robust}}") == "fast and robust"
    assert strip_structural_tex(r"bound $\text{O}(n)$ holds") == r"bound $\text{O}(n)$ holds"


def test_clean_text_joins_hyphenated_line_breaks() -> None:
    assert clean_text("trans-\nformers  are\n neat") == "transformers are neat"
    assert clean_text("para one\n\n\npara  two") == "para one\n\npara two"


def test_looks_truncated_heuristic() -> None:
    assert looks_truncated("We show that...") is True
    assert looks_truncated("We show that…") is True
    assert looks_truncated("We show improvements over") is True
    assert looks_truncated("A complete sentence.") is False
    assert looks_truncated("") is False


def test_recover_abstract_prefers_the_abstract_section() -> None:
    text = "Lead prose.\n\n## Abstract\n\nThe real  abstract.\n\nSecond part.\n\n## Intro\n\nBody."
    assert recover_abstract(text) == "The real abstract. Second part."


def test_recover_abstract_falls_back_to_leading_prose_and_caps() -> None:
    text = "Opening paragraph.\n\n## Introduction\n\nBody."
    assert recover_abstract(text) == "Opening paragraph."
    long = "word " * 600 + "\n\n## Intro\n\nBody."
    assert len(recover_abstract(long)) == 1500
