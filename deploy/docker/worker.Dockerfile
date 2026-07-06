# Event-plane worker image (ingest/embed — pick the worker via the compose command).
# Build from the repo root:
#   docker build -f deploy/docker/worker.Dockerfile .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev --extra kafka --extra airtable
COPY researchscout ./researchscout
RUN uv sync --frozen --no-dev --extra kafka --extra airtable

FROM python:3.12-slim-bookworm
WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/researchscout ./researchscout
COPY config ./config
ENV PATH="/app/.venv/bin:$PATH"
CMD ["scout", "worker", "ingest"]
