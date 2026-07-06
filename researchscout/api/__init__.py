"""HTTP API over the ResearchScout core (FastAPI).

The core stays a plain synchronous library; this package is just another composition root next to
the CLI. Endpoints are ``def`` (not ``async def``) so the blocking core (sync SQLAlchemy, the
in-process embedder, the LLM client) runs on the framework's threadpool.
"""
