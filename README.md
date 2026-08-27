<div align="center">
  <img src="apps/web/public/favicon.svg" alt="ResearchScout" width="80">
  <h1>ResearchScout</h1>
  <p>A radar for AI/ML research: it ingests new papers and the signals gathering around them,
  then scores, clusters, and summarizes to separate breakthrough from noise - so you know
  what is worth reading right now.</p>
  <p>
    <img src="https://img.shields.io/badge/AI%20Research-Radar-ea580c" alt="AI Research Radar">
    <img src="https://img.shields.io/badge/Machine-Learning-9a3412" alt="Machine Learning">
    <img src="https://img.shields.io/badge/Large%20Language-Models-b45309" alt="Large Language Models">
    <img src="https://img.shields.io/badge/arXiv-Signals-6b7280" alt="arXiv Signals">
  </p>
</div>

## What it does

- A scored paper feed with two-axis filters - field (AI/ML, statistics, mathematics, the
  places computing meets other disciplines) and technique (NLP, vision, RL) - where every
  filtered view is a shareable URL.
- Ask Scout about the papers: fast extractive answers with excerpts by default, an AI mode
  that writes grounded, cited prose, and a web search for papers the library lacks.
- Emerging topics clustered from recent papers and ranked by momentum, with trend badges.
- A weekly digest and a daily must-read report, both windowed on when papers actually
  arrived.
- A personalized for-you feed learned from saves, reads, and dismissals.
- A models and benchmarks catalogue (Epoch AI + Hugging Face) where a model whose paper is
  in the corpus links straight to it.
- In-app PDF reading, and math that renders properly in titles and abstracts.

**Scope.** The corpus is computing, statistics and mathematics: a paper belongs if it
carries a `cs`, `stat`, `eess`, `math` or `math-ph` category anywhere in its list.
Cross-lists count - intersection work is exactly what this radar exists to surface.

## Quickstart

Runs as plain host processes - no Docker. Needs Homebrew Postgres with pgvector plus Kafka
(`brew install postgresql@17 pgvector kafka`), [uv](https://docs.astral.sh/uv/), and pnpm.

```bash
make setup    # deps, .env, a repo-local Postgres cluster and Kafka storage in .local/
make start    # postgres, kafka, the streaming pipeline, api, web - prints the URLs when ready
make seed     # ~25 real arXiv papers, embedded and searchable
```

Then open http://localhost:4321. Chat needs a language model: the defaults target local
[Ollama](https://ollama.com) (`ollama pull qwen2.5:3b-instruct`), or point `RS_LLM_*` in
`.env` at any OpenAI-compatible provider. `make stop` shuts everything down; plain `make`
lists every target.

## How it works

arXiv supplies the papers; citations (Semantic Scholar, OpenAlex fallback), Hugging Face
trending, Hacker News, and GitHub code adoption supply the signals. Locally a
streaming pipeline (Kafka + Bytewax, `scout stream serve`) parses, categorizes, and injects
each paper with idempotent upserts; the deployed scheduler drives the same work in batches
on a wall-clock schedule shaped around arXiv's announcement day. Papers are embedded and
chunked into pgvector, scored by multi-signal momentum, and retrieved through hybrid search
with reranking - which is what the feed, Scout's answers, the digests, and the topic
clusters all read. `scout stream tail` watches packets live and `GET /v1/stream/stats`
serves pipeline rollups.

## Backfill

`config/sources.yaml` registers the archives the scope rule covers. To fill the radar,
backfill per archive with the resumable cursor, then embed and pull citations:

```bash
uv run scout ingest --since 2026-01-01 --category 'cs.*' --resume    # repeat per archive
uv run scout index                                                   # embeddings; hours on CPU
uv run scout ingest --source semantic_scholar                        # citation counts
```

A corpus gathered before the scope narrowed can be cleaned with `scout db prune-scope
--dry-run` (without the flag it deletes - the one irreversible command here, so back up
first).

## Development

Requires [uv](https://docs.astral.sh/uv/); Python 3.12 is pinned via `.python-version`.

```bash
uv sync                 # venv plus runtime and dev dependencies
uv run scout --help     # the CLI surface

# checks
uv run ruff check researchscout tests
uv run ruff format --check researchscout tests
uv run mypy researchscout
uv run pytest -q -m "not integration"
```

Configuration is environment variables (prefix `RS_`) or a local `.env` - copy
`.env.example` to start. The canonical data model is `researchscout/schema.py`.

Retrieval changes carry a manual gate: run `make eval` (Recall@10 and nDCG@10 over the
labeled queries in `config/eval_queries.yaml`) before and after any change to retrieval,
ranking, or the embedding model, against the same corpus both times. The numbers move with
the corpus, so compare the before/after delta rather than the absolutes, and update
`config/eval_baseline.md` in the same pull request so the next change has a reference
point. `scout eval draft` seeds new known-item cases; rewrite the drafted titles into real
questions before trusting them.

## Deploying

The public site is the frontend on Vercel plus the backend on Railway - both build from
this repository and auto-deploy on a push to main, so a merge is the whole deploy step.
`deploy/README.md` is the runbook; `deploy/PUBLISHING.md` covers the Railway, Auth0, and
Vercel account setup.

```bash
make deploy-verify   # deployed SHA vs origin/main, migrations, freshness, run ledger
make backup          # manual dump over the Postgres TCP proxy, keeps a week, verifies
```

The pipeline monitors itself: a health task self-checks every half hour (ingest cadence,
failing streaks, weekend-aware freshness, hung runs, retention), `GET /v1/system/status`
serves the verdict, and the site's about page renders it.

## Data sources and attribution

Every connector in `config/sources.yaml` carries an `attribution` block naming the
upstream, its terms, the license its data arrives under, and what this app takes from it.
`GET /v1/sources` serves those blocks (never the credentials beside them) and the web
`/about` page renders them alongside the copyright, API-usage and privacy notices. Adding a
source means adding its attribution: `tests/test_sources.py` fails otherwise.
