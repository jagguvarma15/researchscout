"""Provider-agnostic LLM interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator


class LLM(ABC):
    """A chat/completion model. Any provider hides behind this interface."""

    model: str

    @abstractmethod
    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        """Return the model's text response to a system + user message."""

    def stream(self, system: str, user: str, *, temperature: float = 0.2) -> Iterator[str]:
        """Yield the response in chunks; this default emits ``complete()`` as one chunk, so
        providers (and test fakes) that never override it still work everywhere."""
        yield self.complete(system, user, temperature=temperature)
