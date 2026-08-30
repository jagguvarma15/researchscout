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
import time
from collections.abc import Iterator
from functools import cached_property
from typing import Any

from researchscout.config import get_settings
from researchscout.llm.base import LLM
from researchscout.llm.errors import is_quota_error
from researchscout.llm.usage import LlmCallUsage, current_purpose, record_usage

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
        self._stream_usage = settings.llm_stream_usage
        # Latched on the first server that rejects stream_options, so a strict
        # OpenAI-compatible endpoint costs one retry ever, not one per answer.
        self._stream_usage_broken = False

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
        purpose = current_purpose()
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
            )
        except Exception as exc:
            record_usage(self._usage(purpose, started, exc=exc))
            raise
        usage = getattr(response, "usage", None)
        record_usage(
            self._usage(
                purpose,
                started,
                prompt=getattr(usage, "prompt_tokens", None),
                completion=getattr(usage, "completion_tokens", None),
            )
        )
        return str(response.choices[0].message.content or "")

    def stream(self, system: str, user: str, *, temperature: float = 0.2) -> Iterator[str]:
        # Eager on purpose: the request is created (and the purpose contextvar read) at
        # call time, not on the first next() — the API's SSE loop resumes generators in
        # per-resumption context copies where a late read would see the wrong purpose.
        purpose = current_purpose()
        started = time.perf_counter()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            response = self._create_stream(messages, temperature)
        except Exception as exc:
            record_usage(self._usage(purpose, started, exc=exc))
            raise
        return self._consume(response, purpose, started)

    def _create_stream(self, messages: list[dict[str, str]], temperature: float) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if self._stream_usage and not self._stream_usage_broken:
            kwargs["stream_options"] = {"include_usage": True}
        try:
            return self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            from openai import BadRequestError

            if "stream_options" not in kwargs or not isinstance(exc, BadRequestError):
                raise
            # A strict OpenAI-compatible server that rejects the parameter: answer
            # without token counts rather than not at all, and stop sending it.
            logger.warning("server rejected stream_options.include_usage; disabling it")
            self._stream_usage_broken = True
            del kwargs["stream_options"]
            return self._client.chat.completions.create(**kwargs)

    def _consume(self, response: Any, purpose: str, started: float) -> Iterator[str]:
        prompt: int | None = None
        completion: int | None = None
        outcome = "ok"
        detail: str | None = None
        try:
            for chunk in response:
                # The include_usage final chunk (and keep-alives) carry no choices.
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    prompt = getattr(usage, "prompt_tokens", prompt)
                    completion = getattr(usage, "completion_tokens", completion)
                if chunk.choices and chunk.choices[0].delta.content:
                    yield str(chunk.choices[0].delta.content)
        except GeneratorExit:
            outcome = "aborted"
            raise
        except Exception as exc:
            outcome = "quota" if is_quota_error(exc) else "error"
            detail = repr(exc)[:200]
            raise
        finally:
            record_usage(
                LlmCallUsage(
                    purpose=purpose,
                    model=self.model,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    latency_ms=int((time.perf_counter() - started) * 1000.0),
                    outcome=outcome,
                    detail=detail,
                )
            )

    def _usage(
        self,
        purpose: str,
        started: float,
        *,
        prompt: int | None = None,
        completion: int | None = None,
        exc: BaseException | None = None,
    ) -> LlmCallUsage:
        if exc is None:
            outcome, detail = "ok", None
        else:
            outcome = "quota" if is_quota_error(exc) else "error"
            detail = repr(exc)[:200]
        return LlmCallUsage(
            purpose=purpose,
            model=self.model,
            prompt_tokens=prompt,
            completion_tokens=completion,
            latency_ms=int((time.perf_counter() - started) * 1000.0),
            outcome=outcome,
            detail=detail,
        )
