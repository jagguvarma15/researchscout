from pathlib import Path

import pytest

from researchscout.evaluate import (
    EvalCase,
    benchmark_embedding,
    evaluate_cases,
    load_cases,
    ndcg_at_k,
    recall_at_k,
    save_cases,
)


def test_recall_counts_relevant_in_the_cutoff() -> None:
    ranked = ["a", "b", "c", "d"]
    assert recall_at_k(ranked, {"a", "c"}, k=2) == 0.5
    assert recall_at_k(ranked, {"a", "c"}, k=4) == 1.0
    assert recall_at_k(ranked, {"z"}, k=4) == 0.0
    assert recall_at_k(ranked, set(), k=4) == 0.0


def test_ndcg_rewards_earlier_hits() -> None:
    assert ndcg_at_k(["a", "b"], {"a"}, k=2) == 1.0
    # The single relevant id at position 2: 1/log2(3) against an ideal of 1/log2(2).
    assert ndcg_at_k(["b", "a"], {"a"}, k=2) == pytest.approx(0.6309, abs=1e-3)
    assert ndcg_at_k(["b", "c"], {"a"}, k=2) == 0.0
    assert ndcg_at_k(["b", "c"], set(), k=2) == 0.0


def test_evaluate_cases_scores_each_query() -> None:
    cases = [
        EvalCase(query="one", relevant=("a",)),
        EvalCase(query="two", relevant=("z",)),
    ]
    rankings = {"one": ["a", "b"], "two": ["a", "b"]}
    report = evaluate_cases(cases, lambda query: rankings[query], k=2)
    assert [result.recall for result in report.cases] == [1.0, 0.0]
    assert report.mean_recall == 0.5
    assert report.mean_ndcg == 0.5
    assert report.k == 2


def test_cases_roundtrip_through_yaml(tmp_path: Path) -> None:
    path = tmp_path / "queries.yaml"
    cases = [EvalCase(query="sparse attention", relevant=("arxiv:1", "arxiv:2"))]
    save_cases(path, cases)
    assert load_cases(path) == cases


def test_load_cases_skips_incomplete_entries(tmp_path: Path) -> None:
    path = tmp_path / "queries.yaml"
    path.write_text(
        "cases:\n"
        "  - query: keep\n"
        "    relevant: [arxiv:1]\n"
        "  - query: no relevant ids\n"
        "  - relevant: [arxiv:2]\n",
        encoding="utf-8",
    )
    assert load_cases(path) == [EvalCase(query="keep", relevant=("arxiv:1",))]


def test_benchmark_embedding_covers_every_text_in_batches() -> None:
    batches: list[list[str]] = []
    rate = benchmark_embedding(batches.append, ["t"] * 10, batch_size=4)
    assert [len(batch) for batch in batches] == [4, 4, 2]
    assert rate > 0
