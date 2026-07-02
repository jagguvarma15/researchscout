"""LLM: a provider-agnostic interface and an OpenAI-compatible (Ollama/vLLM) implementation."""

from researchscout.llm.base import LLM
from researchscout.llm.openai_compat import OpenAICompatLLM

__all__ = ["LLM", "OpenAICompatLLM"]
