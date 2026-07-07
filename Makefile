# One-command lifecycle for the local stack. `make` alone lists the targets.
SHELL := /bin/bash
COMPOSE := docker compose
ALL_PROFILES := --profile core --profile events --profile obs --profile airtable

.DEFAULT_GOAL := help

.PHONY: help setup start start-all stop down clean seed digest check logs ps k3d-up k3d-down

help: ## list targets
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[1m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## install toolchains, dependencies, and local config
	@command -v docker >/dev/null || { echo "docker is required: https://docs.docker.com/desktop/"; exit 1; }
	@command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
	@command -v pnpm >/dev/null || { echo "pnpm is required: npm install -g pnpm"; exit 1; }
	@command -v ollama >/dev/null || echo "note: no ollama — chat needs it (brew install ollama; ollama pull qwen2.5:3b-instruct) or a cloud key in .env"
	uv sync
	cd apps/web && pnpm install
	@[ -f .env ] || { cp .env.example .env && echo "created .env from .env.example"; }
	@echo "setup complete — next: make start"

start: ## start the core stack (db, redis, keycloak, api, web) and migrate
	$(COMPOSE) --profile core up -d --build
	@echo "waiting for postgres..."
	@until $(COMPOSE) exec -T db pg_isready -U researchscout >/dev/null 2>&1; do sleep 2; done
	uv run scout db upgrade
	@echo "waiting for keycloak (first boot takes ~30s)..."
	@for i in $$(seq 1 40); do \
	  code=$$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/realms/researchscout/.well-known/openid-configuration); \
	  [ "$$code" = "200" ] && break; sleep 3; done; \
	  [ "$$code" = "200" ] || { echo "keycloak did not come up — check: make logs"; exit 1; }
	@echo
	@echo "  web       http://localhost:4321          (sign in: demo / demo)"
	@echo "  api docs  http://localhost:8000/docs"
	@echo "  keycloak  http://localhost:8080           (admin / admin)"
	@echo
	@echo "  next: make seed   (then chat needs ollama serve + qwen2.5:3b-instruct, or a cloud key in .env)"

start-all: start ## core plus the Kafka event plane (~3.9GB — the 8GB machine's ceiling)
	$(COMPOSE) --profile events up -d --build

seed: ## ingest ~25 recent cs.LG papers and index them
	uv run scout ingest --since $$(uv run python -c "from datetime import UTC, datetime, timedelta; print((datetime.now(UTC)-timedelta(days=7)).date())") --category cs.LG --max 25
	uv run scout index

digest: ## build and publish this week's digest (needs the LLM up)
	uv run scout digest

stop: ## stop all containers and kill stray host dev processes
	-$(COMPOSE) $(ALL_PROFILES) stop
	-@lsof -ti :8000 2>/dev/null | xargs kill -9 2>/dev/null; true
	-@lsof -ti :4321 2>/dev/null | xargs kill -9 2>/dev/null; true
	@echo "stopped"

down: ## remove containers (volumes/data are kept)
	$(COMPOSE) $(ALL_PROFILES) down

clean: ## remove containers AND volumes — deletes all Postgres/Kafka data
	@echo "removing containers and volumes (all local data)..."
	$(COMPOSE) $(ALL_PROFILES) down -v

check: ## everything CI runs: lint, types, unit tests, web check + build
	uv run ruff check researchscout tests
	uv run ruff format --check researchscout tests
	uv run mypy researchscout
	uv run pytest -m "not integration"
	cd apps/web && pnpm astro check && pnpm build

logs: ## tail logs from running services
	$(COMPOSE) $(ALL_PROFILES) logs -f --tail 50

ps: ## show running services
	$(COMPOSE) $(ALL_PROFILES) ps

k3d-up: ## run the whole stack on a disposable k3d cluster
	bash deploy/k3d/up.sh

k3d-down: ## delete the k3d cluster
	bash deploy/k3d/down.sh
