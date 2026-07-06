# researchscout
ResearchScout ingests new AI/ML papers and their surrounding signals (citations, code adoption, social buzz), then scores, clusters, and summarizes them to separate breakthrough from noise - so you know what's worth reading right now.

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
`GET /v1/papers/{id}`, and `POST /v1/ask` (grounded, cited answer). The LLM defaults to local
Ollama; point `RS_LLM_BASE_URL` / `RS_LLM_MODEL` / `RS_LLM_API_KEY` at any OpenAI-compatible
provider to swap it.
