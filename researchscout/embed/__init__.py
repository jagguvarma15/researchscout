"""Embeddings: a provider-agnostic interface and a local sentence-transformers implementation."""

from researchscout.embed.base import Embedder
from researchscout.embed.local import LocalEmbedder

__all__ = ["Embedder", "LocalEmbedder"]
