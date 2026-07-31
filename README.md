# researchscout
ResearchScout ingests new AI/ML papers and their surrounding signals (citations, trending attention, code adoption), then scores, clusters, and summarizes them to separate breakthrough from noise - so you know what's worth reading right now.

## Quickstart

Everything runs as plain host processes — no Docker. Needs Homebrew Postgres with pgvector
plus Kafka (`brew install postgresql@17 pgvector kafka`), [uv](https://docs.astral.sh/uv/), and
pnpm. Chat needs an LLM: the defaults target local [Ollama](https://ollama.com) (`brew install
ollama`, run `ollama serve`, then `ollama pull qwen2.5:3b-instruct` — one-time ~2.3GB), or point
`RS_LLM_*` in `.env` at any OpenAI-compatible provider.

```bash
make setup    # deps, .env, a repo-local Postgres cluster and Kafka storage in .local/
make start    # postgres, kafka, the streaming pipeline, api, web — prints the URLs when ready
make seed     # ~25 real arXiv papers, embedded and searchable
```

Then open http://localhost:4321: browse and search the feed — no sign-in, the app runs as a
built-in local user — and ask the chat drawer about the papers; answers stream with citations.
The Filters button opens a sidebar for extracting papers by subject (tech = computer science
broadly vs everything else on arXiv, or specific groups and categories), date (window or
year/month), popularity (most cited, most active), authors, and venues; results are plain URLs
you can share or bookmark. Read on a card or paper page opens the PDF in-app; math in titles
and abstracts renders properly. Star papers (`/saved`), run `make digest` and visit `/digests`
for the weekly summary. `/topics` clusters recent papers into emerging themes ranked by
momentum, and `/for-you` personalizes the feed from your interests. `make stop` shuts
everything down; `make clean` also wipes the data; plain `make` lists every target.

## Streaming pipeline

Ingestion runs as a streaming pipeline over a local single-node Kafka broker (KRaft mode,
brew-installed, lifecycle-managed by make with data under `.local/`). `scout stream serve`
(started by `make start`) runs polling producers — arXiv hourly, signal sources on their
cadence, full text in politely paced batches — publishing raw packets to `rs.raw.v1`, and a
Bytewax worker consuming them through three stages: parse (deterministic normalization and
cleanup), categorize (taxonomy group, topic-centroid match, statistical keywords with a
small-LLM fallback, optional custom labels), and inject (idempotent upserts, embeddings,
chunks, signals). Every packet carries per-stage lineage into Postgres: `GET /v1/stream/stats`
serves hourly rollups, the `pipeline_rollups_hourly` view is Grafana-ready, and
`scout stream tail` watches packets live on the `rs.parsed.v1` / `rs.enriched.v1` taps.
Delivery is at-least-once with natural-key upserts everywhere, so replays converge.
`make scheduler` still drives the derived products (weekly digest, topics, the daily report
with its must-read five). The batch commands below remain the manual fallback whenever the
broker is down.

## Deep backfill

`config/sources.yaml` registers every arXiv group. The seed pulls one small slice; to fill the
radar, backfill per group with the resumable cursor (arXiv caps paging depth per query, so keep
runs group-sized), then embed and pull citation signals:

```bash
uv run scout ingest --since 2026-01-01 --category 'cs.*' --resume    # repeat per group; rerun on interruption
uv run scout index                                                   # embeddings; hours on CPU, run overnight
uv run scout ingest --source semantic_scholar                        # citations -> citation counts
```

One-time step for rows ingested before the metadata capture landed: re-ingest their original
window once so venue, comment, and the primary category populate (same-id re-ingest refreshes
in place). Ingest paces itself with `RS_ARXIV_PAGE_DELAY_SEC` (default 3 seconds between pages).

## Development

Requires [uv](https://docs.astral.sh/uv/). The project pins Python 3.12 via `.python-version`
(uv will fetch it automatically).

```bash
uv sync                 # create the venv and install runtime + dev dependencies
uv run scout --help     # see the CLI surface

# checks
uv run ruff check researchscout tests
uv run ruff format --check researchscout tests
uv run mypy researchscout
uv run pytest -q -m "not integration"      # unit tests — no Docker needed
```

Configuration is via environment variables (prefix `RS_`) or a local `.env` — copy `.env.example`
to `.env` to start. The canonical data model lives in `researchscout/schema.py`; the source
registry stub is `config/sources.yaml`.

## HTTP API

The `api` extra adds a FastAPI service over the same core the CLI uses (the dev group already
includes it, so `uv sync` is enough for development):

```bash
uv run scout serve api    # http://127.0.0.1:8000, OpenAPI docs at /docs
```

Endpoints: `GET /healthz`, `GET /v1/papers` (recency feed; `?q=` switches to semantic ranking),
`GET /v1/papers/{id}`, `GET /v1/topics` (emerging topics), `GET /v1/me/feed` (personalized),
and `POST /v1/ask` (grounded, cited answer). With `RS_OIDC_ISSUER` unset (the default) the API
runs in local no-auth mode as a built-in user; set an issuer to require OIDC Bearer tokens.
The LLM defaults to local Ollama; point `RS_LLM_BASE_URL` / `RS_LLM_MODEL` / `RS_LLM_API_KEY`
at any OpenAI-compatible provider to swap it.

`GET /v1/papers` filter and sort parameters (all combinable; they also apply under `?q=`):

| Param | Meaning |
|---|---|
| `days` | window in days (mutually exclusive with `year`) |
| `year`, `month` | calendar window; `month` requires `year` |
| `category` | arXiv category, repeatable (`category=cs.LG&category=math.CO`) |
| `kind` | `tech` (cs, stat, eess), `non_tech` (everything else), or `ai` (categories overlap cs.AI, cs.LG, cs.CL, cs.CV, cs.NE, stat.ML — cross-lists count) |
| `group` | taxonomy group key, repeatable (`cs`, `stat`, `eess`, `math`, `physics`, `q-bio`, `q-fin`, `econ`) |
| `author`, `venue` | case-insensitive contains match |
| `min_citations` | latest citation count at least N |
| `sort` | `newest` (default), `citations`, or `activity` |
| `limit`, `offset` | pagination; the response carries `total` (null under `q`) |

```bash
curl 'http://127.0.0.1:8000/v1/papers?kind=tech&year=2026&sort=citations&limit=5'
```
