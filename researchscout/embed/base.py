"""Provider-agnostic embedding interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Embedder(ABC):
    """Vectorizes text for semantic retrieval. Any implementation hides behind this interface."""

    model_id: str
    dim: int

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed documents (papers) for indexing."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed a search query (may apply a model-specific instruction prefix)."""

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        """Embed several queries at once; implementations override to batch the forward pass."""
        return [self.embed_query(text) for text in texts]
