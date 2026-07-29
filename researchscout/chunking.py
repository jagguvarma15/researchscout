"""Section-aware chunking of extracted full text (pure).

Chunks never cross a section boundary — a retrieval hit should be one coherent passage, not
the tail of Methods glued to the head of Results. Within a section, whole paragraphs
accumulate to roughly ``target_words`` (about 400 wordpieces, safely inside bge-small's
512-token window) with a short trailing overlap carried into the next chunk so sentences that
straddle a cut stay findable. The section headings come from the extractor's "## " markers.
"""

from __future__ import annotations

from dataclasses import dataclass

_TARGET_WORDS = 300
_OVERLAP_WORDS = 45
_MIN_TAIL_WORDS = 40
_MAX_CHUNKS = 120


@dataclass(frozen=True)
class Chunk:
    index: int
    section: str | None
    text: str


def _sections(text: str) -> list[tuple[str | None, list[str]]]:
    """(heading, paragraphs) in document order; prose before any heading gets None."""
    sections: list[tuple[str | None, list[str]]] = []
    heading: str | None = None
    paragraphs: list[str] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("## "):
            if paragraphs:
                sections.append((heading, paragraphs))
            heading = block[3:].strip() or None
            paragraphs = []
        else:
            paragraphs.append(block)
    if paragraphs:
        sections.append((heading, paragraphs))
    return sections


def chunk_text(
    text: str,
    *,
    target_words: int = _TARGET_WORDS,
    overlap_words: int = _OVERLAP_WORDS,
    max_chunks: int = _MAX_CHUNKS,
) -> list[Chunk]:
    """Split marked-up full text into section-bounded, overlapping chunks.

    The carried overlap never counts toward the target budget or the tiny-tail judgment —
    otherwise it would both shrink every chunk and launder a few leftover words into a
    "big enough" standalone chunk (or get duplicated by a merge).
    """
    chunks: list[Chunk] = []
    for heading, paragraphs in _sections(text):
        carry = ""
        fresh: list[str] = []
        fresh_words = 0
        for paragraph in paragraphs:
            words = len(paragraph.split())
            if fresh and fresh_words + words > target_words:
                emitted = " ".join(([carry] if carry else []) + fresh)
                chunks.append(Chunk(index=len(chunks), section=heading, text=emitted))
                carry = " ".join(emitted.split()[-overlap_words:])
                fresh, fresh_words = [], 0
            fresh.append(paragraph)
            fresh_words += words
        if fresh:
            # A tiny leftover reads better appended to the previous chunk of the same section.
            if chunks and chunks[-1].section == heading and fresh_words < _MIN_TAIL_WORDS:
                merged = chunks[-1]
                chunks[-1] = Chunk(
                    index=merged.index,
                    section=heading,
                    text=f"{merged.text} {' '.join(fresh)}",
                )
            else:
                emitted = " ".join(([carry] if carry else []) + fresh)
                chunks.append(Chunk(index=len(chunks), section=heading, text=emitted))
        if len(chunks) >= max_chunks:
            break
    return chunks[:max_chunks]
