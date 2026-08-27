"""Local embeddings via sentence-transformers (BGE-small-en-v1.5).

The model is loaded lazily on first use, so importing this module is cheap (no torch import until an
embedding is actually requested). BGE asks for an instruction prefix on queries only, not documents;
other models get no prefix, so eval A/Bs across models stay fair.
"""

from __future__ import annotations

from functools import cached_property
from typing import Any

from researchscout.embed.base import Embedder
from researchscout.modelgate import model_slot

_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def query_prefix_for(model_id: str) -> str:
    """The query-side instruction prefix a model expects ("" for models that use none)."""
    if model_id.startswith("BAAI/bge-") and "-en" in model_id:
        return _BGE_QUERY_PREFIX
    return ""


class LocalEmbedder(Embedder):
    """BGE-small-en-v1.5 via sentence-transformers; normalized vectors, MPS when available.

    ``device``/``backend`` exist for the eval harness (``scout eval embed-speed``): forcing
    cpu vs mps, or the onnx backend (needs a manual optimum[onnxruntime] install), without
    changing the defaults the rest of the app uses.
    """

    def __init__(
        self,
        model_id: str = "BAAI/bge-small-en-v1.5",
        dim: int = 384,
        *,
        device: str | None = None,
        backend: str = "torch",
    ) -> None:
        self.model_id = model_id
        self.dim = dim
        self._device = device
        self._backend = backend

    @cached_property
    def _model(self) -> Any:
        import torch
        from sentence_transformers import SentenceTransformer

        device = self._device or ("mps" if torch.backends.mps.is_available() else "cpu")
        return SentenceTransformer(self.model_id, device=device, backend=self._backend)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # This instance is a process-wide singleton called from the request threadpool and
        # the scheduler at once; the slot keeps concurrent passes bounded.
        with model_slot():
            vectors = self._model.encode(texts, normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        with model_slot():
            vector = self._model.encode(
                query_prefix_for(self.model_id) + text, normalize_embeddings=True
            )
        return list(vector.tolist())
