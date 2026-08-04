"""FastAPI application: the HTTP composition root, mirroring the CLI's."""

from __future__ import annotations

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
    keywords,
    me,
    papers,
    profile,
    saved,
    sources,
    stream,
    topics,
    webimport,
)
from researchscout.api.service_auth import service_token_middleware


def create_app() -> FastAPI:
    """Build the API app: routers under ``/v1``, liveness probe at ``/healthz``."""
    app = FastAPI(title="ResearchScout API", version=__version__)
    # Before routing: with RS_SERVICE_TOKEN set, only callers carrying it get past the door.
    app.middleware("http")(service_token_middleware)
    app.include_router(papers.router, prefix="/v1")
    app.include_router(catalog.router, prefix="/v1")
    app.include_router(ask.router, prefix="/v1")
    app.include_router(chat.router, prefix="/v1")
    app.include_router(saved.router, prefix="/v1")
    app.include_router(feed.router, prefix="/v1")
    app.include_router(digests.router, prefix="/v1")
    app.include_router(topics.router, prefix="/v1")
    app.include_router(keywords.router, prefix="/v1")
    app.include_router(sources.router, prefix="/v1")
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
