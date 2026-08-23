"""LLM backed by an OpenAI-compatible endpoint.

Points at local Ollama by default (``http://localhost:11434/v1``). Any OpenAI-compatible server
(a hosted provider, a self-hosted vLLM) works by changing ``base_url`` alone — no code changes. The
client is created lazily, so importing this module doesn't require a running server.

When the LangSmith env contract is present (``LANGSMITH_TRACING=true`` plus an API key, the
SDK's own standard names), the client is wrapped so every completion — ask, chat, digest,
topic labels, guardrail, keyword fallback — traces with its model, tokens, and latency.
Absent, or with the package missing, the bare client is byte-identical to before.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from functools import cached_property
from typing import Any

from researchscout.config import get_settings
from researchscout.llm.base import LLM

logger = logging.getLogger(__name__)

# OpenRouter reads these attribution headers; every other provider ignores them.
_CLIENT_HEADERS = {
    "HTTP-Referer": "https://github.com/jagguvarma15/researchscout",
    "X-Title": "ResearchScout",
}


def _maybe_trace(client: Any) -> Any:
    """Wrap the client for LangSmith when tracing is asked for; the bare client otherwise."""
    if os.environ.get("LANGSMITH_TRACING", "").lower() != "true":
        return client
    try:
        from langsmith.wrappers import wrap_openai
    except ImportError:
        logger.warning("LANGSMITH_TRACING is set but langsmith is not installed; tracing off")
        return client
    return wrap_openai(client)


class OpenAICompatLLM(LLM):
    """Chat completions over any OpenAI-compatible server (Ollama now, vLLM later)."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = base_url or settings.llm_base_url
        self.model = model or settings.llm_model
        self._api_key = api_key or settings.llm_api_key
        self._timeout = settings.llm_timeout_sec
        self._max_retries = settings.llm_max_retries

    @cached_property
    def _client(self) -> Any:
        from openai import OpenAI

        return _maybe_trace(
            OpenAI(
                base_url=self.base_url,
                api_key=self._api_key,
                default_headers=_CLIENT_HEADERS,
                timeout=self._timeout,
                max_retries=self._max_retries,
            )
        )

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

    def stream(self, system: str, user: str, *, temperature: float = 0.2) -> Iterator[str]:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            stream=True,
        )
        for chunk in response:
            # Some providers send keep-alive chunks with no choices.
            if chunk.choices and chunk.choices[0].delta.content:
                yield str(chunk.choices[0].delta.content)
