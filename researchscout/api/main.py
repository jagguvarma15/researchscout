"""FastAPI application: the HTTP composition root, mirroring the CLI's."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from researchscout import __version__
from researchscout.api.routers import (
    account,
    ask,
    catalog,
    chat,
    digests,
    events,
    feed,
    highlights,
    keywords,
    me,
    papers,
    profile,
    saved,
    sources,
    stream,
    system,
    topics,
    trends,
    webimport,
)
from researchscout.api.service_auth import service_token_middleware
from researchscout.config import get_settings

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Build the API app: routers under ``/v1``, liveness probe at ``/healthz``."""
    settings = get_settings()
    if settings.service_token and not settings.oidc_issuer:
        # The same condition health.check_auth_posture warns on, repeated at startup so the
        # deploy log carries it even before the first status read.
        logger.warning(
            "RS_SERVICE_TOKEN is set but RS_OIDC_ISSUER is empty: "
            "every caller shares the built-in local account"
        )
    app = FastAPI(title="ResearchScout API", version=__version__)
    # Before routing: with RS_SERVICE_TOKEN set, only callers carrying it get past the door.
    app.middleware("http")(service_token_middleware)
    app.include_router(papers.router, prefix="/v1")
    app.include_router(catalog.router, prefix="/v1")
    app.include_router(ask.router, prefix="/v1")
    app.include_router(chat.router, prefix="/v1")
    app.include_router(saved.router, prefix="/v1")
    app.include_router(highlights.router, prefix="/v1")
    app.include_router(feed.router, prefix="/v1")
    app.include_router(digests.router, prefix="/v1")
    app.include_router(topics.router, prefix="/v1")
    app.include_router(trends.router, prefix="/v1")
    app.include_router(keywords.router, prefix="/v1")
    app.include_router(sources.router, prefix="/v1")
    app.include_router(system.router, prefix="/v1")
    app.include_router(profile.router, prefix="/v1")
    app.include_router(me.router, prefix="/v1")
    app.include_router(account.router, prefix="/v1")
    app.include_router(events.router, prefix="/v1")
    app.include_router(stream.router, prefix="/v1")
    app.include_router(webimport.router, prefix="/v1")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
