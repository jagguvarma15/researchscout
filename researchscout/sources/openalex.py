"""OpenAlex citation counts: the fallback lane behind Semantic Scholar.

Every arXiv paper carries a DataCite DOI (``10.48550/arXiv.<id>``), and OpenAlex resolves
those, so papers batch into pipe-separated DOI filters (50 per request — a few hundred of the
keyed 100k/day credit budget). OpenAlex has required an API key since early 2026: set
``OPENALEX_API_KEY`` (or ``api_key`` in sources.yaml); without one the connector declines to
run rather than hammer 401s. Counts are observed as ``citation`` signals under this source.
Double counting against Semantic Scholar is prevented twice over: the citation walker lets
only one source refresh a paper per day, and breakthrough scoring treats ``citation`` as an
exclusive type — only the freshest source's series counts.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from researchscout.schema import Signal, SignalType, normalize_arxiv_id
from researchscout.sources.base import HealthStatus, RawItem, Source, register, source_config
from researchscout.useragent import default_headers

logger = logging.getLogger(__name__)

_WORKS = "https://api.openalex.org/works"
_REQUEST_TIMEOUT = 30.0
_BATCH = 50
_MAX_BATCHES = 40
_BATCH_DELAY_SEC = 0.3

_DOI_ARXIV_RE = re.compile(r"10\.48550/arxiv\.(\S+)", re.IGNORECASE)


@register
class OpenAlexSource(Source):
    name = "openalex"
    kind = "signal"

    def __init__(self, api_key: str | None = None) -> None:
        cfg = source_config(self.name)
        self._api_key: str | None = (
            api_key or cfg.get("api_key") or os.environ.get("OPENALEX_API_KEY") or None
        )
        self._mailto = cfg.get("mailto")
        self._warned_keyless = False

    def _require_key(self) -> bool:
        """True when a key is present; log the refusal once per instance otherwise."""
        if self._api_key:
            return True
        if not self._warned_keyless:
            logger.warning(
                "openalex: no API key configured (OPENALEX_API_KEY); keys are mandatory "
                "upstream, skipping"
            )
            self._warned_keyless = True
        return False

    def _stored_arxiv_ids(self) -> dict[str, str]:
        """Every stored arXiv id -> canonical paper id."""
        from researchscout.store.db import session_scope
        from researchscout.store.models import ExternalIdRow

        with session_scope() as session:
            rows = session.execute(
                select(ExternalIdRow.value, ExternalIdRow.paper_id).where(
                    ExternalIdRow.scheme == "arxiv"
                )
            ).all()
        return {value: paper_id for value, paper_id in rows}

    def _batch_counts(self, arxiv_ids: list[str]) -> dict[str, int]:
        """One DOI-filter request: arXiv id -> cited_by_count for the ids OpenAlex resolves."""
        dois = "|".join(f"10.48550/arXiv.{arxiv_id}" for arxiv_id in arxiv_ids)
        params: dict[str, Any] = {
            "filter": f"doi:{dois}",
            "select": "doi,cited_by_count",
            "per-page": _BATCH,
            "api_key": self._api_key,
        }
        if self._mailto:
            params["mailto"] = str(self._mailto)
        resp = httpx.get(
            _WORKS,
            params=params,
            headers=default_headers(),
            timeout=_REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        counts: dict[str, int] = {}
        for work in resp.json().get("results") or []:
            match = _DOI_ARXIV_RE.search(str(work.get("doi") or ""))
            if match:
                counts[normalize_arxiv_id(match.group(1))] = int(work.get("cited_by_count") or 0)
        return counts

    def citations_for(self, pairs: list[tuple[str, str]]) -> list[Signal]:
        """Citation signals for explicit (paper_id, arxiv_id) pairs — the walker's entry point.

        Returns nothing without an API key. Papers OpenAlex does not resolve are simply
        absent. Raises ``httpx.HTTPError`` on upstream failure for the caller to treat as
        stop-here-keep-progress.
        """
        if not self._require_key():
            return []
        fetched_at = datetime.now(UTC)
        signals: list[Signal] = []
        for begin in range(0, len(pairs), _BATCH):
            batch = pairs[begin : begin + _BATCH]
            counts = self._batch_counts([arxiv_id for _, arxiv_id in batch])
            for paper_id, arxiv_id in batch:
                count = counts.get(normalize_arxiv_id(arxiv_id))
                if count is None:
                    continue
                raw = RawItem(
                    source=self.name,
                    fetched_at=fetched_at,
                    payload={"paper_id": paper_id, "cited_by_count": count},
                )
                signals.append(self.normalize(raw))
            if begin + _BATCH < len(pairs):
                time.sleep(_BATCH_DELAY_SEC)
        return signals

    def fetch(self, since: datetime, cursor: str | None) -> tuple[list[RawItem], str | None]:
        """Whole-corpus snapshot walk — the manual ``scout ingest`` path.

        The scheduler drives the watermark walker (``ingest/citations.py``) instead.
        """
        if not self._require_key():
            return [], None
        fetched_at = datetime.now(UTC)
        stored = self._stored_arxiv_ids()
        arxiv_ids = sorted(stored)[: _BATCH * _MAX_BATCHES]

        items: list[RawItem] = []
        for begin in range(0, len(arxiv_ids), _BATCH):
            batch = arxiv_ids[begin : begin + _BATCH]
            counts = self._batch_counts(batch)
            for arxiv_id, count in counts.items():
                paper_id = stored.get(arxiv_id)
                if paper_id is None:
                    continue
                items.append(
                    RawItem(
                        source=self.name,
                        fetched_at=fetched_at,
                        payload={"paper_id": paper_id, "cited_by_count": count},
                    )
                )
            if begin + _BATCH < len(arxiv_ids):
                time.sleep(_BATCH_DELAY_SEC)
        return items, None

    def normalize(self, raw: RawItem) -> Signal:
        payload = raw.payload
        return Signal(
            paper_id=str(payload["paper_id"]),
            type=SignalType.citation,
            source=self.name,
            value=float(payload["cited_by_count"]),
            metadata={},
            observed_at=raw.fetched_at,
        )

    def health(self) -> HealthStatus:
        params: dict[str, Any] = {"filter": "doi:10.48550/arXiv.1706.03762", "select": "doi"}
        if self._api_key:
            params["api_key"] = self._api_key
        try:
            resp = httpx.get(
                _WORKS,
                params=params,
                headers=default_headers(),
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return "error"
        if resp.status_code == 429:
            return "rate_limited"
        return "ok" if resp.is_success else "error"
