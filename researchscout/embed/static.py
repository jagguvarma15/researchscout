"""Static keyword embedder over model2vec: token-lookup embeddings, no transformer.

Scoring keyword candidates does not need bge quality - it needs a consistent space and
speed. potion-base-8M embeds by averaging precomputed token vectors, roughly two orders
of magnitude faster than a bge forward pass on this hardware. It is 256-dimensional, so
it can never mix with the stored 384-dim bge space: when selected, BOTH sides of the
keyword similarity (candidates and a scoring-only document vector) come from this model,
while the stored paper embedding and topic matching stay bge.
"""

from __future__ import annotations

from functools import cached_property
from typing import Any

from researchscout.embed.base import Embedder


class StaticKeywordEmbedder(Embedder):
    """model2vec potion-base-8M (MIT, ~30MB), lazily downloaded on first use."""

    model_id = "minishlab/potion-base-8M"
    dim = 256

    @cached_property
    def _model(self) -> Any:
        from model2vec import StaticModel  # the optional static-embed extra

        return StaticModel.from_pretrained(self.model_id)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.encode(texts)]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
