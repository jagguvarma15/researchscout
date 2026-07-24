# API service image. Build from the repo root:
#   docker build -f docker/api.Dockerfile .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev --extra api --extra obs
COPY researchscout ./researchscout
RUN uv sync --frozen --no-dev --extra api --extra obs

FROM python:3.12-slim-bookworm
WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/researchscout ./researchscout
COPY alembic.ini ./
COPY alembic ./alembic
COPY config ./config
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "researchscout.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
