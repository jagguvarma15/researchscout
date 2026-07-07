"""FastAPI application: the HTTP composition root, mirroring the CLI's."""

from __future__ import annotations

from fastapi import FastAPI

from researchscout import __version__
from researchscout.api.routers import ask, chat, digests, papers, profile, saved
from researchscout.obs.otel import init_otel, instrument_app


def create_app() -> FastAPI:
    """Build the API app: routers under ``/v1``, liveness probe at ``/healthz``."""
    init_otel("researchscout-api")
    app = FastAPI(title="ResearchScout API", version=__version__)
    app.include_router(papers.router, prefix="/v1")
    app.include_router(ask.router, prefix="/v1")
    app.include_router(chat.router, prefix="/v1")
    app.include_router(saved.router, prefix="/v1")
    app.include_router(digests.router, prefix="/v1")
    app.include_router(profile.router, prefix="/v1")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    instrument_app(app)
    return app


app = create_app()
