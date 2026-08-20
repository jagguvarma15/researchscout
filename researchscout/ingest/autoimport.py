"""Land papers that a curated signal names but the corpus lacks.

Signal sources attach observations to stored papers only, so a paper trending before the
nightly ingest reaches it silently drops its first - and steepest - observations. This
module closes that gap for sources trusted enough to auto-import from (Hugging Face's
curated daily list; never open forums). The landing path is the web import's own
``land_entry``, plus the scope check the ingest pipeline applies: nothing outside the
radar's subject lands, whoever recommends it. No user, no save, no embedding - the
pipeline's categorize and index tasks pick the paper up on their next run.
"""

from __future__ import annotations

import logging
import time

import httpx

from researchscout.config import get_settings
from researchscout.importing import fetch_arxiv_entry, land_entry
from researchscout.sources.arxiv import _normalize_payload
from researchscout.store.db import session_scope
from researchscout.taxonomy import in_scope

logger = logging.getLogger(__name__)

# One curated list's worth per run: the bound keeps a bad upstream day from turning the
# signal poll into a bulk ingest.
_MAX_PER_RUN = 25


def land_unknown_papers(arxiv_ids: list[str]) -> dict[str, str]:
    """Fetch, scope-check, and store the given arXiv ids; returns arxiv id -> paper id.

    Paced like every other arXiv walk (fetch_arxiv_entry bypasses the source's pace
    lock, so the delay is applied here), bounded per run, and fail-open: an id that
    cannot be fetched or parses badly is skipped, and an unreachable arXiv ends the run
    quietly - the ids come back on the next poll.
    """
    settings = get_settings()
    landed: dict[str, str] = {}
    for index, arxiv_id in enumerate(arxiv_ids[:_MAX_PER_RUN]):
        if index and settings.arxiv_page_delay_sec > 0:
            time.sleep(settings.arxiv_page_delay_sec)
        try:
            payload = fetch_arxiv_entry(arxiv_id)
        except httpx.HTTPError:
            logger.warning("auto-import: arXiv unreachable; ending this run")
            break
        if payload is None:
            continue
        try:
            paper = _normalize_payload(payload)
        except ValueError:
            logger.warning("auto-import: skipping malformed entry %s", arxiv_id)
            continue
        if not in_scope(paper.categories):
            continue
        with session_scope() as session:
            paper_id, _, _ = land_entry(session, payload)
        landed[arxiv_id] = paper_id
    if landed:
        logger.info("auto-import: landed %d paper(s)", len(landed))
    return landed
