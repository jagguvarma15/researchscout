"""Targeted full-text capture from arXiv's native HTML, with ar5iv as the fallback.

arXiv renders HTML for every TeX submission since 2023-12 (~90% of the corpus); ar5iv covers
most older papers. Extraction walks headings and paragraphs in document order and marks each
section boundary with a "## " line — exactly what the section-aware chunker keys on later.
Full-content harvesting of arXiv is not permitted, so fetching stays targeted (saved and
interacted-with papers first, capped batches) and paced like the ingest pipeline.
"""

from __future__ import annotations

import httpx

from researchscout.useragent import default_headers

_ARXIV_HTML = "https://arxiv.org/html/{arxiv_id}"
_AR5IV_HTML = "https://ar5iv.labs.arxiv.org/html/{arxiv_id}"
_REQUEST_TIMEOUT = 60.0
# Shorter than this and the page was an error shell or a stub, not an article.
_MIN_ARTICLE_CHARS = 500

_SECTION_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# LaTeXML wraps list-item and caption prose in <p class="ltx_p"> too, so paragraphs alone
# cover the body without double-counting nested containers.
_TEXT_TAGS = {"p", "figcaption"}
_DROP_SELECTORS = ("script", "style", "math", "svg", "nav", "footer")


def extract_text(html: str) -> str:
    """Headings and paragraphs in document order; headings become "## " section markers."""
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    for selector in _DROP_SELECTORS:
        for node in tree.css(selector):
            node.decompose()
    root = tree.css_first("article") or tree.body
    if root is None:
        return ""
    parts: list[str] = []
    for node in root.traverse(include_text=False):
        if node.tag in _SECTION_TAGS:
            heading = _prose(node)
            if heading:
                parts.append(f"## {heading}")
        elif node.tag in _TEXT_TAGS:
            text = _prose(node)
            if text:
                parts.append(text)
    return "\n\n".join(parts)


def _prose(node: object) -> str:
    """Inline text with source spacing kept intact and runs of whitespace collapsed.

    A separator between text nodes would split around inline tags ("everywhere</em>." turns
    into "everywhere ."), so the raw concatenation is normalized instead.
    """
    return " ".join(node.text(separator="", strip=False).split())  # type: ignore[attr-defined]


def fetch_full_text(arxiv_id: str) -> str | None:
    """The extracted article text, arXiv HTML first and ar5iv second; None when neither has it."""
    for url in (_ARXIV_HTML.format(arxiv_id=arxiv_id), _AR5IV_HTML.format(arxiv_id=arxiv_id)):
        try:
            resp = httpx.get(
                url,
                headers=default_headers(),
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError:
            continue
        if resp.status_code != 200:
            continue
        text = extract_text(resp.text)
        if len(text) >= _MIN_ARTICLE_CHARS:
            return text
    return None
