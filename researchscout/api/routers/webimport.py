"""Web search across external paper sources and one-click single-paper import.

The search endpoint 404s when RS_WEB_SEARCH_ENABLED is off (the kill switch); the import
endpoint stays available regardless - importing a known arXiv id is a library feature,
not a search feature. Both share one rate-limit bucket.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_embedder, get_session
from researchscout.api.ratelimit import check_rate_limit
from researchscout.api.schemas import (
    ImportRequest,
    ImportResponse,
    WebSearchHit,
    WebSearchResponse,
)
from researchscout.config import get_settings
from researchscout.embed.base import Embedder
from researchscout.importing import fetch_arxiv_entry, import_paper, publish_enrichment
from researchscout.schema import normalize_arxiv_id
from researchscout.store.papers import find_by_external_id
from researchscout.websearch import web_search

router = APIRouter(tags=["webimport"])


@router.get("/search/web")
def search_web(
    q: Annotated[str, Query(min_length=1, max_length=300)],
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> WebSearchResponse:
    """Search arXiv and Semantic Scholar for papers not in the library."""
    settings = get_settings()
    if not settings.web_search_enabled:
        raise HTTPException(status_code=404, detail="web search is disabled")
    check_rate_limit(
        f"websearch:{user.sub}",
        limit=settings.web_search_rate_limit,
        window_seconds=settings.web_search_rate_window_seconds,
    )
    hits, failed = web_search(q)
    out = []
    for hit in hits:
        known = find_by_external_id(session, "arxiv", hit.arxiv_id) if hit.arxiv_id else None
        out.append(
            WebSearchHit(
                provider=hit.provider,
                title=hit.title,
                authors=hit.authors,
                year=hit.year,
                snippet=hit.snippet,
                arxiv_id=hit.arxiv_id,
                url=hit.url,
                already_known=known is not None,
                paper_id=known,
            )
        )
    return WebSearchResponse(query=q, hits=out, providers_failed=failed)


@router.post("/papers/import")
def import_arxiv(
    body: ImportRequest,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
) -> ImportResponse:
    """Import one arXiv paper: land it embedded now, save it, queue stream enrichment.

    The synchronous embed is what makes the paper vector-searchable immediately - the
    deployment has no stream worker to do it later. The enrichment envelope stays
    best-effort for stacks that do run one.
    """
    settings = get_settings()
    check_rate_limit(
        f"websearch:{user.sub}",
        limit=settings.web_search_rate_limit,
        window_seconds=settings.web_search_rate_window_seconds,
    )
    arxiv_id = normalize_arxiv_id(body.arxiv_id.strip())
    try:
        payload = fetch_arxiv_entry(arxiv_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="arXiv is unreachable") from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="unknown arXiv id")
    paper_id, title, already_known, embedded = import_paper(session, user.sub, payload, embedder)
    queued = publish_enrichment(settings, payload)
    return ImportResponse(
        id=paper_id,
        title=title,
        already_known=already_known,
        enrichment_queued=queued,
        embedded=embedded,
    )
