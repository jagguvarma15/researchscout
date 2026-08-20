# The backend image: one container runs the API and the scheduler together via
# `scout serve all`. Railway builds it straight from the repo (see railway.json); by hand:
#   docker build -f docker/api.Dockerfile -t researchscout-api .
#
# It is a large image - torch and the sentence-transformers stack are runtime dependencies,
# because embedding happens in process - but not as large as it would be: pyproject.toml
# resolves torch from the CPU index on Linux, which drops about fifteen CUDA packages a
# container on a laptop would never load. The model weights ARE baked in: a redeploy on a
# platform without shared volumes would otherwise refetch them on every cold start, and the
# healthcheck would spend its whole grace period watching a download.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
# The torch wheel alone is a few hundred megabytes; the 30s default gives up on a slow link
# part-way through and fails the whole build.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_HTTP_TIMEOUT=180

# One extra beyond the API, named at build time. Empty is the deployment: ingestion runs
# from the scheduler in batches, so it has no use for a broker client. Pass "stream" to get
# an image that can run `scout stream serve`.
ARG EXTRAS=

# Dependencies first, so a code change does not re-resolve or re-download them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev --extra api --extra observe ${EXTRAS:+--extra "$EXTRAS"}

# Then the project, installed rather than linked, so the runtime stage needs nothing but the
# virtualenv.
COPY researchscout ./researchscout
RUN uv sync --frozen --no-dev --extra api --extra observe ${EXTRAS:+--extra "$EXTRAS"} --no-editable

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

USER scout

# Bake the embedder and reranker weights into the image (~150MB) so a fresh container is
# serving within seconds of boot instead of downloading models inside its healthcheck grace
# period. The ids mirror the config defaults; a model swap means rebuilding the image.
RUN python -c "\
from sentence_transformers import CrossEncoder, SentenceTransformer; \
SentenceTransformer('BAAI/bge-small-en-v1.5'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

EXPOSE 8000

# Shell form so the platform's injected PORT wins; 8000 for a plain `docker run`.
CMD ["sh", "-c", "scout serve all --host 0.0.0.0 --port ${PORT:-8000}"]
