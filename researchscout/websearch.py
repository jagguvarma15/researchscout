"""Free-text paper search across external sources (arXiv, Semantic Scholar).

The fallback when the local corpus has nothing relevant: each provider is best-effort
(one failing never hides the other), results merge deduplicated by arXiv id with the
arXiv-provider hit winning, and only a handful come back - this feeds result cards, not a
crawler. Semantic Scholar rate-limits unauthenticated traffic aggressively; S2_API_KEY is
honored exactly like the signals source honors it.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

import feedparser
import httpx

from researchscout.schema import normalize_arxiv_id
from researchscout.sources.arxiv import _API_URL, _arxiv_id_from_url, _entry_payload

logger = logging.getLogger(__name__)

_S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_S2_FIELDS = "title,abstract,year,authors,externalIds,url"
_TIMEOUT = 10.0
_PER_PROVIDER = 5
_MERGED_CAP = 10
_SNIPPET_CHARS = 300
_AUTHORS_SHOWN = 5


@dataclass(frozen=True)
class WebHit:
    provider: Literal["arxiv", "s2"]
    title: str
    authors: list[str]
    year: int | None
    snippet: str
    arxiv_id: str | None
    url: str | None


def search_arxiv(
    query: str, *, limit: int = _PER_PROVIDER, timeout: float = _TIMEOUT
) -> list[WebHit]:
    """Relevance-sorted arXiv API search."""
    params = {
        "search_query": f'all:"{query}"',
        "start": "0",
        "max_results": str(limit),
        "sortBy": "relevance",
    }
    resp = httpx.get(_API_URL, params=params, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)
    hits = []
    for entry in feed.entries:
        payload = _entry_payload(entry)
        raw_id = _arxiv_id_from_url(payload.get("id"))
        published = str(payload.get("published") or "")
        hits.append(
            WebHit(
                provider="arxiv",
                title=" ".join(str(payload.get("title") or "").split()),
                authors=list(payload.get("authors") or [])[:_AUTHORS_SHOWN],
                year=int(published[:4]) if published[:4].isdigit() else None,
                snippet=" ".join(str(payload.get("summary") or "").split())[:_SNIPPET_CHARS],
                arxiv_id=normalize_arxiv_id(raw_id) if raw_id else None,
                url=payload.get("id"),
            )
        )
    return hits


def search_s2(
    query: str,
    *,
    limit: int = _PER_PROVIDER,
    timeout: float = _TIMEOUT,
    api_key: str | None = None,
) -> list[WebHit]:
    """Semantic Scholar paper search (better semantic ranking; strict free-tier limits)."""
    headers = {"x-api-key": api_key} if api_key else {}
    resp = httpx.get(
        _S2_SEARCH_URL,
        params={"query": query, "limit": str(limit), "fields": _S2_FIELDS},
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
    )
    resp.raise_for_status()
    hits = []
    for item in resp.json().get("data") or []:
        external = item.get("externalIds") or {}
        raw_arxiv = external.get("ArXiv")
        abstract = item.get("abstract") or ""
        hits.append(
            WebHit(
                provider="s2",
                title=item.get("title") or "",
                authors=[
                    author["name"] for author in item.get("authors") or [] if author.get("name")
                ][:_AUTHORS_SHOWN],
                year=item.get("year"),
                snippet=" ".join(abstract.split())[:_SNIPPET_CHARS],
                arxiv_id=normalize_arxiv_id(raw_arxiv) if raw_arxiv else None,
                url=item.get("url"),
            )
        )
    return hits


def web_search(query: str, *, api_key: str | None = None) -> tuple[list[WebHit], list[str]]:
    """Merged best-effort hits plus the providers that failed.

    arXiv order first (its hits carry importable ids), deduplicated by arXiv id, capped.
    """
    api_key = api_key or os.environ.get("S2_API_KEY")
    hits: list[WebHit] = []
    failed: list[str] = []
    try:
        hits.extend(search_arxiv(query))
    except Exception:  # noqa: BLE001 - one provider must never hide the other
        logger.warning("arxiv web search failed", exc_info=True)
        failed.append("arxiv")
    try:
        hits.extend(search_s2(query, api_key=api_key))
    except Exception:  # noqa: BLE001
        logger.warning("s2 web search failed", exc_info=True)
        failed.append("s2")

    seen: set[str] = set()
    merged: list[WebHit] = []
    for hit in hits:
        key = hit.arxiv_id or f"{hit.provider}:{hit.title.lower()}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(hit)
    return merged[:_MERGED_CAP], failed
