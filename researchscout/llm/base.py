"""Provider-agnostic LLM interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLM(ABC):
    """A chat/completion model. Any provider hides behind this interface."""

    model: str

    @abstractmethod
    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        """Return the model's text response to a system + user message."""
