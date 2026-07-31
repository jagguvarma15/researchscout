"""Settings-honoring construction of the shared embedder."""

from __future__ import annotations

from functools import lru_cache

from researchscout.embed.base import Embedder


@lru_cache(maxsize=1)
def default_embedder() -> Embedder:
    """The process-wide embedder, resolving the model from RS_EMBEDDING_MODEL.

    Every vector query filters on model_id, so query-side and index-side construction
    must agree on the model; a bare LocalEmbedder() pins the built-in default and would
    return zero hits after a model switch. Cached so the model loads once per process.
    Imports stay inside so importing this module never pulls in torch.
    """
    from researchscout.config import get_settings
    from researchscout.embed.local import LocalEmbedder

    return LocalEmbedder(get_settings().embedding_model)
