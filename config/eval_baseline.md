# Retrieval eval baseline

The reference point for the manual gate in the README: run `make eval` before and after
any retrieval, ranking, or embedding change and compare the deltas against this file,
then update it in the same pull request.

## 2026-08-27

- Corpus: production (21,819 papers), first-stage retrieval only (`--no-rerank`)
- Command: `scout eval retrieval -k 10` over `config/eval_queries.yaml` (30 cases)

| Model | Mean recall@10 | Mean nDCG@10 |
|---|---|---|
| BAAI/bge-small-en-v1.5 | 0.400 | 0.287 |

Reading the number: retrieval multiplies similarity by recency, so known-item cases decay
as their target papers age — the ten cases written against the fresh corpus all recall
their paper, while the twenty July-2026 cases mostly no longer surface theirs inside the
top ten against a corpus with thousands of newer near-matches. That decay is the ranking
working as designed, not a regression; it is also why the gate compares before/after
deltas on the same day and the same corpus, never absolutes across weeks. When most fresh
cases start missing too, that is a real retrieval problem.
