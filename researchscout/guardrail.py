"""Chat scope guardrail: a one-word LLM pre-check that keeps chat on research topics.

Chat-only by design: the CLI ``scout ask`` and ``/v1/ask`` never run the classifier. Every
failure path fails open (in scope) so a flaky local model can never block a real question.
"""

from __future__ import annotations

import re

from researchscout.llm.base import LLM
from researchscout.trace import trace_span

_GUARDRAIL_SYSTEM = (
    "You decide whether a question is about research in artificial intelligence, computer "
    "science, or adjacent technical fields (machine learning, statistics, mathematics, "
    "engineering, or the scientific literature about them). "
    "Answer with exactly one word: yes or no."
)

REFUSAL_TEXT = (
    "I can only help with questions about AI, computer science, and related technical "
    "research. Ask me about the papers on the radar."
)

_FIRST_WORD_RE = re.compile(r"[A-Za-z]+")


def is_research_question(llm: LLM, question: str) -> bool:
    """True unless the classifier answers a clean no (fail open on every other path)."""
    with trace_span("guardrail") as span:
        try:
            reply = llm.complete(_GUARDRAIL_SYSTEM, question, temperature=0.0)
        except Exception:
            span["verdict"] = "error"
            return True
        match = _FIRST_WORD_RE.search(reply)
        word = match.group(0).lower() if match else ""
        span["verdict"] = word or "empty"
        return word != "no"
