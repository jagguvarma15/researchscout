"""LLM backed by an OpenAI-compatible endpoint.

Points at local Ollama by default (``http://localhost:11434/v1``). At Stage 5 the same interface
targets a self-hosted vLLM server — only ``base_url`` changes, no code changes. The client is
created lazily, so importing this module doesn't require a running server.
"""

from __future__ import annotations

from functools import cached_property
from typing import Any

from researchscout.config import get_settings
from researchscout.llm.base import LLM


class OpenAICompatLLM(LLM):
    """Chat completions over any OpenAI-compatible server (Ollama now, vLLM later)."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str = "ollama",
    ) -> None:
        settings = get_settings()
        self.base_url = base_url or settings.llm_base_url
        self.model = model or settings.llm_model
        self._api_key = api_key

    @cached_property
    def _client(self) -> Any:
        from openai import OpenAI

        return OpenAI(base_url=self.base_url, api_key=self._api_key)

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
        return str(response.choices[0].message.content or "")
