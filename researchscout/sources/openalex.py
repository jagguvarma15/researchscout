"""OpenAlex citation counts: a keyless cross-check and outage insurance for Semantic Scholar.

Every arXiv paper carries a DataCite DOI (``10.48550/arXiv.<id>``), and OpenAlex resolves
those, so stored papers batch into pipe-separated DOI filters (50 per request, well inside the
keyless 100k/day budget; set ``mailto`` in sources.yaml to join the polite pool). Counts are
observed as ``citation`` signals under this source — per-source series scoring keeps them
separate from Semantic Scholar's, which is also why the source ships disabled: with both on,
the citation contribution to the breakthrough score is counted twice.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from researchscout.schema import Signal, SignalType, normalize_arxiv_id
from researchscout.sources.base import HealthStatus, RawItem, Source, register, source_config

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

    def fetch(self, since: datetime, cursor: str | None) -> tuple[list[RawItem], str | None]:
        fetched_at = datetime.now(UTC)
        mailto = source_config(self.name).get("mailto")
        stored = self._stored_arxiv_ids()
        arxiv_ids = sorted(stored)[: _BATCH * _MAX_BATCHES]

        items: list[RawItem] = []
        for begin in range(0, len(arxiv_ids), _BATCH):
            batch = arxiv_ids[begin : begin + _BATCH]
            dois = "|".join(f"10.48550/arXiv.{arxiv_id}" for arxiv_id in batch)
            params: dict[str, Any] = {
                "filter": f"doi:{dois}",
                "select": "doi,cited_by_count",
                "per-page": _BATCH,
            }
            if mailto:
                params["mailto"] = str(mailto)
            resp = httpx.get(_WORKS, params=params, timeout=_REQUEST_TIMEOUT, follow_redirects=True)
            resp.raise_for_status()
            for work in resp.json().get("results") or []:
                match = _DOI_ARXIV_RE.search(str(work.get("doi") or ""))
                if not match:
                    continue
                paper_id = stored.get(normalize_arxiv_id(match.group(1)))
                if paper_id is None:
                    continue
                items.append(
                    RawItem(
                        source=self.name,
                        fetched_at=fetched_at,
                        payload={
                            "paper_id": paper_id,
                            "cited_by_count": int(work.get("cited_by_count") or 0),
                        },
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
        try:
            resp = httpx.get(
                _WORKS,
                params={"filter": "doi:10.48550/arXiv.1706.03762", "select": "doi"},
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return "error"
        if resp.status_code == 429:
            return "rate_limited"
        return "ok" if resp.is_success else "error"
