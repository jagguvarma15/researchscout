"""Deterministic parse stage: registry normalization plus regex cleanup, never an LLM.

Paper packets are normalized through the same Source.normalize the batch pipeline uses, then
title and abstract get a conservative cleanup: NFKC normalization, line-break dehyphenation,
whitespace collapsing, and structural TeX stripping that leaves inline math verbatim. Signal
packets just normalize. Fulltext packets gain their ordered section headings. Failures never
drop a packet: the lineage stamp records the error and the envelope keeps flowing to the taps.
"""

from __future__ import annotations

import re
import unicodedata

from researchscout.chunking import section_headings
from researchscout.schema import Paper, Signal
from researchscout.sources import get_source
from researchscout.sources.base import RawItem
from researchscout.stream.envelope import Envelope

# \emph{x} / \textbf{x} style wrappers whose braces carry no meaning of their own.
_TEX_COMMAND = re.compile(
    r"\\(?:emph|textbf|textit|texttt|textsc|textrm|text)\{([^{}]*)\}",
)
# {\it x} / {\bf x} style group wrappers.
_TEX_GROUP = re.compile(r"\{\\(?:it|bf|em|tt|sc)\s+([^{}]*)\}")
_MATH_SEGMENT = re.compile(r"(\$[^$]*\$)")
_LINE_BREAK_HYPHEN = re.compile(r"(\w)-\n\s*(\w)")

_ABSTRACT_MAX_CHARS = 1500


def strip_structural_tex(text: str) -> str:
    """Unwrap emphasis-style TeX outside math; inline ``$...$`` stays verbatim."""
    parts = _MATH_SEGMENT.split(text)
    for index, part in enumerate(parts):
        if part.startswith("$"):
            continue
        for pattern in (_TEX_COMMAND, _TEX_GROUP):
            while True:
                replaced = pattern.sub(r"\1", part)
                if replaced == part:
                    break
                part = replaced
        parts[index] = part
    return "".join(parts)


def clean_text(text: str) -> str:
    """NFKC-normalize, join line-break hyphenation, and collapse whitespace per paragraph."""
    text = unicodedata.normalize("NFKC", text)
    text = _LINE_BREAK_HYPHEN.sub(r"\1\2", text)
    paragraphs = re.split(r"\n\s*\n", text)
    return "\n\n".join(" ".join(p.split()) for p in paragraphs if p.strip())


def looks_truncated(abstract: str) -> bool:
    """A cheap truncation heuristic: trailing ellipsis or a missing terminal punctuation."""
    stripped = abstract.rstrip()
    if not stripped:
        return False
    if stripped.endswith(("...", "\u2026")):
        return True
    return stripped[-1] not in ".!?)\"'"


def recover_abstract(full_text: str, *, max_chars: int = _ABSTRACT_MAX_CHARS) -> str:
    """Reconstruct an abstract from extracted full text.

    Prefers the paragraphs under an Abstract heading; falls back to the prose before the
    first heading. Used when a paper arrives without an abstract of its own.
    """
    blocks = [block.strip() for block in full_text.split("\n\n") if block.strip()]
    collected: list[str] = []
    in_abstract = False
    for block in blocks:
        if block.startswith("## "):
            if in_abstract:
                break
            in_abstract = block[3:].strip().lower() == "abstract"
            continue
        if in_abstract:
            collected.append(block)
    if not collected:
        for block in blocks:
            if block.startswith("## "):
                break
            collected.append(block)
    text = " ".join(" ".join(block.split()) for block in collected)
    return text[:max_chars]


def _clean_field(text: str) -> str:
    return clean_text(strip_structural_tex(text))


def _parse_paper(envelope: Envelope) -> None:
    raw = RawItem(
        source=envelope.source, fetched_at=envelope.fetched_at, payload=envelope.payload["raw"]
    )
    normalized = get_source(envelope.source).normalize(raw)
    if not isinstance(normalized, Paper):
        raise TypeError(f"{envelope.source} normalized a paper packet into {type(normalized)}")
    paper = normalized.model_copy(
        update={
            "title": _clean_field(normalized.title),
            "abstract": _clean_field(normalized.abstract),
        }
    )
    envelope.payload["paper"] = paper.model_dump(mode="json")
    envelope.payload["abstract_truncated"] = looks_truncated(paper.abstract)


def _parse_signal(envelope: Envelope) -> None:
    raw = RawItem(
        source=envelope.source, fetched_at=envelope.fetched_at, payload=envelope.payload["raw"]
    )
    normalized = get_source(envelope.source).normalize(raw)
    if not isinstance(normalized, Signal):
        raise TypeError(f"{envelope.source} normalized a signal packet into {type(normalized)}")
    envelope.payload["signal"] = normalized.model_dump(mode="json")


def _parse_fulltext(envelope: Envelope) -> None:
    envelope.payload["sections"] = section_headings(envelope.payload["text"])


def parse_stage(envelope: Envelope) -> Envelope:
    """Stamp and run the parse step for one packet; errors are recorded, never raised."""
    stamp = envelope.begin("parse")
    try:
        if envelope.kind == "paper":
            _parse_paper(envelope)
        elif envelope.kind == "signal":
            _parse_signal(envelope)
        else:
            _parse_fulltext(envelope)
    except Exception as exc:  # noqa: BLE001 - a bad packet must not stop the flow
        envelope.finish(stamp, "error", f"{type(exc).__name__}: {exc}")
    else:
        envelope.finish(stamp)
    return envelope
