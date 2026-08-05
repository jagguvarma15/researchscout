"""arXiv content source — the first connector.

Fetches recent submissions from the arXiv API (Atom), one page per ``fetch`` call (cursor =
pagination offset), and normalizes each entry into a canonical ``Paper``. ``fetch`` (network) and
``normalize`` (pure) are deliberately split so normalization is testable from a saved fixture.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import feedparser
import httpx

from researchscout.config import get_settings
from researchscout.schema import Author, Paper, canonical_id, normalize_arxiv_id
from researchscout.sources.base import (
    HealthStatus,
    RawItem,
    Source,
    register,
    retry_wait,
    source_config,
)
from researchscout.useragent import default_headers

_API_URL = "https://export.arxiv.org/api/query"
_DEFAULT_CATEGORIES = ("cs.LG", "cs.AI", "cs.CL")
_REQUEST_TIMEOUT = 30.0
# Load-shedding responses worth one more try. Everything else raises immediately: a 400 will
# not get better, and retrying it just spends the pacing budget.
_RETRY_STATUSES = frozenset({429, 503})
_RETRY_MAX = 2
_RETRY_WAIT_CAP = 120.0

# arXiv asks for no more than one request every three seconds on a single connection. The
# floor is per process, not per fetch: paging, a second category's first page and a health
# probe all pass through here, and the lock is held across the wait so concurrent callers
# queue rather than burst. Tests reset _last_request_at directly.
_pace_lock = threading.Lock()
_last_request_at: float | None = None


def _to_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


@register
class ArxivSource(Source):
    name = "arxiv"
    kind = "content"

    def __init__(
        self,
        categories: list[str] | None = None,
        page_size: int = 100,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        cfg_categories = source_config(self.name).get("categories")
        self.categories: list[str] = categories or cfg_categories or list(_DEFAULT_CATEGORIES)
        self.page_size = page_size
        self._sleep = sleep
        self._clock = clock

    def _pace(self) -> None:
        """Wait out the remaining request floor, then claim the slot for this request."""
        global _last_request_at
        delay = get_settings().arxiv_page_delay_sec
        with _pace_lock:
            now = self._clock()
            if _last_request_at is not None and delay > 0:
                wait = delay - (now - _last_request_at)
                if wait > 0:
                    self._sleep(wait)
                    now = self._clock()
            _last_request_at = now

    def _search_query(self, since: datetime) -> str:
        cats = " OR ".join(f"cat:{c}" for c in self.categories)
        lo = _to_utc(since).strftime("%Y%m%d%H%M")
        hi = datetime.now(UTC).strftime("%Y%m%d%H%M")
        return f"({cats}) AND submittedDate:[{lo} TO {hi}]"

    def _get(self, params: dict[str, str]) -> httpx.Response:
        """One paced GET, with brief bounded retries when arXiv sheds load.

        429 and 503 usually carry a Retry-After; honor it up to a cap and fall back to a short
        doubling wait without one. After the bounded attempts the error surfaces — the pipeline
        treats it as stop-here-keep-progress, so holding the run open longer buys nothing.
        """
        for attempt in range(_RETRY_MAX + 1):
            self._pace()
            resp = httpx.get(
                _API_URL,
                params=params,
                headers=default_headers(),
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
            if resp.status_code not in _RETRY_STATUSES or attempt == _RETRY_MAX:
                resp.raise_for_status()
                return resp
            self._sleep(retry_wait(resp.headers.get("Retry-After"), attempt, cap=_RETRY_WAIT_CAP))
        raise AssertionError("unreachable")

    def fetch(self, since: datetime, cursor: str | None) -> tuple[list[RawItem], str | None]:
        start = int(cursor) if cursor else 0
        resp = self._get(
            {
                "search_query": self._search_query(since),
                "start": str(start),
                "max_results": str(self.page_size),
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
        )
        feed = feedparser.parse(resp.text)
        fetched_at = datetime.now(UTC)
        items = [
            RawItem(source=self.name, fetched_at=fetched_at, payload=_entry_payload(entry))
            for entry in feed.entries
        ]
        next_cursor = str(start + self.page_size) if len(items) == self.page_size else None
        return items, next_cursor

    def normalize(self, raw: RawItem) -> Paper:
        return _normalize_payload(raw.payload)

    def health(self) -> HealthStatus:
        self._pace()
        try:
            resp = httpx.get(
                _API_URL,
                params={"search_query": "all", "max_results": "1"},
                headers=default_headers(),
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return "error"
        if resp.status_code == 429:
            return "rate_limited"
        return "ok" if resp.is_success else "error"


def _entry_payload(entry: Any) -> dict[str, Any]:
    """Extract the fields we need from a feedparser Atom entry into a plain dict."""
    return {
        "id": entry.get("id"),
        "title": entry.get("title"),
        "summary": entry.get("summary"),
        # Per-author affiliations are not recoverable here: feedparser flattens
        # arxiv:affiliation onto the entry, losing the author association.
        "authors": [a.get("name") for a in entry.get("authors", []) if a.get("name")],
        "categories": [t.get("term") for t in entry.get("tags", []) if t.get("term")],
        "primary_category": (entry.get("arxiv_primary_category") or {}).get("term"),
        "comment": entry.get("arxiv_comment"),
        "journal_ref": entry.get("arxiv_journal_ref"),
        "doi": entry.get("arxiv_doi"),
        "published": entry.get("published"),
        "updated": entry.get("updated"),
        "links": [
            {
                "href": link.get("href"),
                "type": link.get("type"),
                "rel": link.get("rel"),
                "title": link.get("title"),
            }
            for link in entry.get("links", [])
        ],
    }


def _arxiv_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    tail = urlparse(url).path.rsplit("/", 1)[-1]
    return tail or None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _collapse_paragraphs(text: str) -> str:
    """Collapse whitespace per paragraph, preserving blank-line breaks. LaTeX stays verbatim."""
    paragraphs = re.split(r"\n\s*\n", text)
    return "\n\n".join(" ".join(p.split()) for p in paragraphs if p.strip())


def _pdf_url(links: list[dict[str, Any]]) -> str | None:
    for link in links:
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            href = link.get("href")
            return href if isinstance(href, str) else None
    return None


def _normalize_payload(payload: dict[str, Any]) -> Paper:
    raw_id = _arxiv_id_from_url(payload.get("id"))
    if not raw_id:
        raise ValueError("arXiv entry is missing an id")
    bare_id = normalize_arxiv_id(raw_id)
    external_ids = {"arxiv": bare_id}
    doi = payload.get("doi")
    if doi:
        external_ids["doi"] = doi.strip().lower()

    title = " ".join((payload.get("title") or "").split())
    abstract = _collapse_paragraphs(payload.get("summary") or "")
    authors = [Author(name=name) for name in payload.get("authors", [])]

    published = _parse_dt(payload.get("published"))
    if published is None:
        raise ValueError(f"arXiv entry {bare_id} is missing a published date")

    categories = list(payload.get("categories", []))
    primary = payload.get("primary_category") or (categories[0] if categories else None)

    return Paper(
        id=canonical_id(external_ids, title, authors),
        external_ids=external_ids,
        title=title,
        abstract=abstract,
        authors=authors,
        categories=categories,
        primary_category=primary,
        venue=payload.get("journal_ref"),
        comment=payload.get("comment"),
        published_at=published,
        updated_at=_parse_dt(payload.get("updated")),
        source="arxiv",
        url=payload.get("id"),
        pdf_url=_pdf_url(payload.get("links", [])),
    )
