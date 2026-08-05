# The backend image: the API, the scheduler and the stream worker all run from it - same code,
# different command. Build from the repo root:
#   docker build -f docker/api.Dockerfile -t researchscout-api .
#
# It is a large image - torch and the sentence-transformers stack are runtime dependencies,
# because embedding happens in process - but not as large as it would be: pyproject.toml
# resolves torch from the CPU index on Linux, which drops about fifteen CUDA packages a
# container on a laptop would never load. The model weights are not baked in either; they
# download on first use into the cache below, which compose keeps in a volume so a restart
# does not refetch them.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
# The torch wheel alone is a few hundred megabytes; the 30s default gives up on a slow link
# part-way through and fails the whole build.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_HTTP_TIMEOUT=180

# One extra beyond the API, named at build time. Empty is the deployment: it drives ingestion
# from the scheduler in batches, so it has no use for a broker client. Compose's stream service
# passes "stream" and tags the result separately, because an image that can run
# `scout stream serve` is a different image.
ARG EXTRAS=

# Dependencies first, so a code change does not re-resolve or re-download them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev --extra api ${EXTRAS:+--extra "$EXTRAS"}

# Then the project, installed rather than linked, so the runtime stage needs nothing but the
# virtualenv.
COPY researchscout ./researchscout
RUN uv sync --frozen --no-dev --extra api ${EXTRAS:+--extra "$EXTRAS"} --no-editable

FROM python:3.12-slim-bookworm
WORKDIR /app

# Nothing here needs root, and a container that cannot write outside its cache is one less
# thing to think about.
RUN useradd --create-home --uid 10001 scout

# The package itself lives in the virtualenv; only the files that are not part of the wheel
# are copied beside it - migrations and the source registry, both read relative to the
# working directory.
COPY --from=builder --chown=scout:scout /app/.venv ./.venv
COPY --chown=scout:scout alembic.ini ./
COPY --chown=scout:scout alembic ./alembic
COPY --chown=scout:scout config ./config

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/home/scout/.cache/huggingface

# Which commit this image is. make deploy-build stamps it and /v1/system/status serves it, so
# a stale deployment is a readable fact rather than a guess. Empty when built by hand.
ARG GIT_SHA=
ENV RS_BUILD_SHA=$GIT_SHA

# The model cache is a named volume in compose. Docker seeds a fresh volume from whatever the
# image has at that path, ownership included - without this directory it creates one owned by
# root and the unprivileged process cannot write the weights it just downloaded.
RUN mkdir -p /home/scout/.cache/huggingface && chown -R scout:scout /home/scout/.cache

USER scout
EXPOSE 8000

# Overridden per service in compose; this is the one that serves traffic.
CMD ["scout", "serve", "api", "--host", "0.0.0.0", "--port", "8000"]
