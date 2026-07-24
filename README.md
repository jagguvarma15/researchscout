# researchscout
ResearchScout ingests new AI/ML papers and their surrounding signals (citations, trending attention, code adoption), then scores, clusters, and summarizes them to separate breakthrough from noise - so you know what's worth reading right now.

## Quickstart

Needs Docker, [uv](https://docs.astral.sh/uv/), and pnpm. Chat needs an LLM: the defaults
target local [Ollama](https://ollama.com) (`brew install ollama`, run `ollama serve`, then
`ollama pull qwen2.5:3b-instruct` — one-time ~2.3GB), or point `RS_LLM_*` in `.env` at any
OpenAI-compatible provider.

```bash
make setup    # deps + .env
make start    # db, redis, keycloak, api, web — prints the URLs when ready
make seed     # ~25 real arXiv papers, embedded and searchable
```

Then open http://localhost:4321: browse and search the feed, sign in as `demo` / `demo`, and
ask the chat drawer about the papers — answers stream with citations. Star papers (`/saved`),
run `make digest` and visit `/digests` for the weekly summary. `/topics` clusters recent papers
into emerging themes ranked by momentum, and signed-in readers get a personalized `/for-you` feed
from their interests. `scout serve scheduler` (or the `scheduler` compose profile) keeps ingest,
signals, embeddings, digests, and topics refreshing on their own. `make start-all` adds the Kafka
event plane (`scout jobs emit-ingest` then `make logs` to watch papers flow through the
workers). `make stop` shuts everything down; `make clean` also wipes the data; plain `make`
lists every target.

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
uv run scout serve api                     # http://127.0.0.1:8000, OpenAPI docs at /docs
docker compose --profile core up --build   # containerized: db + api on :8000
```

Endpoints: `GET /healthz`, `GET /v1/papers` (recency feed; `?q=` switches to semantic ranking),
`GET /v1/papers/{id}`, `GET /v1/topics` (emerging topics), `GET /v1/me/feed` (personalized,
authenticated), and `POST /v1/ask` (grounded, cited answer). The LLM defaults to local
Ollama; point `RS_LLM_BASE_URL` / `RS_LLM_MODEL` / `RS_LLM_API_KEY` at any OpenAI-compatible
provider to swap it.
