"""Unit checks for the optional model2vec keyword embedder (no model download)."""

import pytest

pytest.importorskip("model2vec")

from researchscout.embed.base import Embedder
from researchscout.embed.static import StaticKeywordEmbedder


def test_static_embedder_declares_its_space() -> None:
    embedder = StaticKeywordEmbedder()
    assert isinstance(embedder, Embedder)
    assert embedder.model_id == "minishlab/potion-base-8M"
    assert embedder.dim == 256  # never mixes with the stored 384-dim bge space
