"""FastAPI dependencies: request-scoped DB sessions and process-wide singletons.

The embedder and LLM are expensive or stateless — one of each per process. They are constructed
lazily (imports stay inside the functions, like the CLI) so importing the API package never pulls
in torch, and tests can override the dependencies without touching them.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.llm.base import LLM
from researchscout.store.db import session_scope


def get_session() -> Iterator[Session]:
    """Yield a request-scoped session (commit on success, rollback on error)."""
    with session_scope() as session:
        yield session


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    from researchscout.embed.local import LocalEmbedder

    return LocalEmbedder()


@lru_cache(maxsize=1)
def get_llm() -> LLM:
    from researchscout.llm.openai_compat import OpenAICompatLLM

    return OpenAICompatLLM()
