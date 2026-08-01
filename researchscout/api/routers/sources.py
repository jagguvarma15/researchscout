"""The data sources behind the feed, with their attribution.

A public read like the topics and keywords routes: it serves config, touches neither the
database nor the network, and backs the /about page's source table so the page always shows
what is actually enabled. Only the attribution block and the enabled flag are exposed - the
credentials that also live in a source's config block (api_key, token, mailto) never are.
"""

from __future__ import annotations

from fastapi import APIRouter

from researchscout.api.schemas import SourceInfo, SourceList
from researchscout.sources.base import describe_sources

router = APIRouter(tags=["sources"])


@router.get("/sources")
def sources_index() -> SourceList:
    items = [
        SourceInfo(
            name=source.name,
            kind=source.kind,
            enabled=source.enabled,
            display_name=source.attribution.name if source.attribution else None,
            homepage=source.attribution.homepage if source.attribution else None,
            terms_url=source.attribution.terms if source.attribution else None,
            data_license=source.attribution.data_license if source.attribution else None,
            provides=source.attribution.provides if source.attribution else None,
        )
        for source in describe_sources()
    ]
    return SourceList(items=items)
