"""Retrieval evaluation: labeled query sets, ranking metrics, and a speed benchmark.

The measuring stick for any embedding or retrieval change — the roadmap gates model switches
on a measured local win, never on leaderboard numbers. Query sets are hand-checkable YAML (a
``cases`` list of ``{query, relevant: [paper ids]}``). Everything here is pure; the CLI wires
in retrieval and the store.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class EvalCase:
    query: str
    relevant: tuple[str, ...]


@dataclass(frozen=True)
class CaseResult:
    query: str
    recall: float
    ndcg: float


@dataclass(frozen=True)
class EvalReport:
    k: int
    cases: tuple[CaseResult, ...]

    @property
    def mean_recall(self) -> float:
        return sum(case.recall for case in self.cases) / len(self.cases) if self.cases else 0.0

    @property
    def mean_ndcg(self) -> float:
        return sum(case.ndcg for case in self.cases) / len(self.cases) if self.cases else 0.0


def recall_at_k(ranked: Sequence[str], relevant: Collection[str], k: int) -> float:
    """Fraction of the relevant set found in the top ``k`` (0 when nothing is relevant)."""
    if not relevant:
        return 0.0
    top = set(ranked[:k])
    return sum(1 for paper_id in relevant if paper_id in top) / len(relevant)


def ndcg_at_k(ranked: Sequence[str], relevant: Collection[str], k: int) -> float:
    """Binary-gain nDCG@k: order-aware, 1.0 when the relevant ids lead the ranking."""
    if not relevant:
        return 0.0
    gains = sum(
        1.0 / math.log2(position + 2)
        for position, paper_id in enumerate(ranked[:k])
        if paper_id in relevant
    )
    ideal = sum(1.0 / math.log2(position + 2) for position in range(min(len(relevant), k)))
    return gains / ideal if ideal else 0.0


def evaluate_cases(
    cases: Sequence[EvalCase], ranker: Callable[[str], Sequence[str]], *, k: int
) -> EvalReport:
    """Run every case's query through ``ranker`` (query -> ranked paper ids) and score it."""
    results = []
    for case in cases:
        ranked = list(ranker(case.query))
        results.append(
            CaseResult(
                query=case.query,
                recall=recall_at_k(ranked, case.relevant, k),
                ndcg=ndcg_at_k(ranked, case.relevant, k),
            )
        )
    return EvalReport(k=k, cases=tuple(results))


def load_cases(path: Path) -> list[EvalCase]:
    """Read a YAML query set; entries without a query or relevant ids are skipped."""
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = payload.get("cases") if isinstance(payload, dict) else payload
    cases: list[EvalCase] = []
    for entry in entries or []:
        query = str(entry.get("query", "")).strip()
        relevant = tuple(str(paper_id) for paper_id in entry.get("relevant") or [])
        if query and relevant:
            cases.append(EvalCase(query=query, relevant=relevant))
    return cases


def save_cases(path: Path, cases: Sequence[EvalCase]) -> None:
    """Write a query set as hand-editable YAML (queries first, stable order)."""
    payload = {"cases": [{"query": case.query, "relevant": list(case.relevant)} for case in cases]}
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def benchmark_embedding(
    embed: Callable[[list[str]], object], texts: Sequence[str], *, batch_size: int
) -> float:
    """Documents per second over one timed pass (warm the model up before calling)."""
    start = time.perf_counter()
    for begin in range(0, len(texts), batch_size):
        embed(list(texts[begin : begin + batch_size]))
    elapsed = time.perf_counter() - start
    return len(texts) / elapsed if elapsed > 0 else 0.0
