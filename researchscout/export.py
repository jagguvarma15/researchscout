"""Export a reading list: BibTeX for reference managers, CSV for everything else.

Pure text builders over stored rows - the router streams whatever these return. The
BibTeX shape matches the paper page's citation block so a copied entry and an exported
one agree on their key and fields.
"""

from __future__ import annotations

import csv
import io
import re

from researchscout.schema import Paper
from researchscout.store.saved import SavedEntry

_KEY_RE = re.compile(r"[^a-zA-Z0-9]+")


def _bibtex_key(paper_id: str) -> str:
    return _KEY_RE.sub("-", paper_id).strip("-")


def bibtex_entry(paper: Paper) -> str:
    """One @article entry, keyed by the canonical id the way the paper page renders it."""
    lines = [
        f"@article{{{_bibtex_key(paper.id)},",
        f"  title = {{{paper.title}}},",
        f"  author = {{{' and '.join(author.name for author in paper.authors)}}},",
        f"  year = {{{paper.published_at.year}}},",
    ]
    if paper.id.startswith("arxiv:"):
        lines.append(f"  eprint = {{{paper.id.removeprefix('arxiv:')}}},")
    if paper.venue:
        lines.append(f"  journal = {{{paper.venue}}},")
    if paper.url:
        lines.append(f"  url = {{{paper.url}}},")
    lines.append("}")
    return "\n".join(lines)


def bibtex_export(entries: list[SavedEntry]) -> str:
    """The whole list as BibTeX, one blank line between entries."""
    return "\n\n".join(bibtex_entry(entry.paper) for entry in entries) + ("\n" if entries else "")


def csv_export(entries: list[SavedEntry]) -> str:
    """The whole list as CSV, library fields included."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        ["id", "title", "authors", "published", "venue", "status", "tags", "note", "url"]
    )
    for entry in entries:
        paper = entry.paper
        writer.writerow(
            [
                paper.id,
                paper.title,
                "; ".join(author.name for author in paper.authors),
                paper.published_at.date().isoformat(),
                paper.venue or "",
                entry.status,
                "; ".join(entry.tags),
                entry.note or "",
                paper.url or "",
            ]
        )
    return out.getvalue()
