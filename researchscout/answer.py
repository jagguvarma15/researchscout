"""Grounded, cited answers: retrieve within the freshness window, then synthesize citing only those.

The synthesis is constrained to the retrieved papers, and a post-check drops any cited id the model
invented (a hallucinated citation never survives into ``Answer.cited``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.llm.base import LLM
from researchscout.obs.trace import trace_span
from researchscout.retrieve.search import ScoredPaper, retrieve

_SYSTEM_PROMPT = (
    "You are a research assistant. Answer ONLY from the papers provided below. "
    "For every claim, cite the paper id in square brackets, e.g. [arxiv:2401.12345]. "
    "Never invent ids or facts. If the papers do not cover the question, say so plainly. "
    "For each relevant paper, briefly cover what's new, why it matters, and what it beats."
)

_CITATION_RE = re.compile(r"\[([a-z]+:[^\]]+)\]")


@dataclass
class Answer:
    text: str
    cited: list[str]
    hallucinated: list[str]
    used: list[ScoredPaper]


def _context(papers: list[ScoredPaper]) -> str:
    return "\n\n".join(
        f"[{item.paper.id}] {item.paper.title}\n{item.paper.abstract}" for item in papers
    )


def answer(
    session: Session,
    embedder: Embedder,
    llm: LLM,
    question: str,
    *,
    k: int = 8,
    days: int | None = None,
) -> Answer:
    """Retrieve recent papers and synthesize a grounded, cited answer."""
    with trace_span("ask", question=question, k=k, days=days) as span:
        used = retrieve(session, embedder, question, k=k, days=days)
        span["retrieved"] = len(used)
        if not used:
            return Answer(
                text="No recent papers match this question.", cited=[], hallucinated=[], used=[]
            )

        user_prompt = f"Question: {question}\n\nPapers:\n{_context(used)}"
        text = llm.complete(_SYSTEM_PROMPT, user_prompt)
        span["model"] = llm.model

        found = list(dict.fromkeys(_CITATION_RE.findall(text)))
        valid = {item.paper.id for item in used}
        cited = [cid for cid in found if cid in valid]
        hallucinated = [cid for cid in found if cid not in valid]
        span["cited"] = len(cited)
        span["hallucinated"] = len(hallucinated)
        return Answer(text=text, cited=cited, hallucinated=hallucinated, used=used)
