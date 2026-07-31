# Host-process lifecycle for the local stack: Postgres (repo-local data dir), the API, and the
# web app all run as plain processes — no containers. `make` alone lists the targets.
SHELL := /bin/bash

PG_VER  ?= 17
PG_BIN  := $(shell brew --prefix postgresql@$(PG_VER) 2>/dev/null)/bin
LOCAL   := $(CURDIR)/.local
PGDATA  := $(LOCAL)/pgdata
LOG     := $(LOCAL)/log
RUN     := $(LOCAL)/run
DB_USER := researchscout
DB_NAME := researchscout

KAFKA_BIN  := $(shell brew --prefix kafka 2>/dev/null)/bin
KAFKA_DATA := $(LOCAL)/kafka-logs
KAFKA_CONF := $(LOCAL)/kafka/server.properties
KAFKA_HEAP ?= -Xmx512m -Xms128m

.DEFAULT_GOAL := help
.PHONY: help setup start stop status logs seed digest scheduler kafka-start kafka-stop check clean

help: ## list targets
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## install toolchains, dependencies, and the local Postgres cluster
	@command -v brew >/dev/null || { echo "Homebrew is required: https://brew.sh"; exit 1; }
	@command -v uv   >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
	@command -v pnpm >/dev/null || { echo "pnpm is required: npm install -g pnpm"; exit 1; }
	@[ -x "$(PG_BIN)/initdb" ] || { echo "postgresql@$(PG_VER) is required: brew install postgresql@$(PG_VER) pgvector"; exit 1; }
	@[ -f "$$(brew --prefix)/share/postgresql@$(PG_VER)/extension/vector.control" ] || \
	  { echo "pgvector is required: brew install pgvector"; exit 1; }
	@command -v ollama >/dev/null || echo "note: no ollama — chat needs it (brew install ollama; ollama pull qwen2.5:3b-instruct) or a cloud key in .env"
	@[ -x "$(KAFKA_BIN)/kafka-storage" ] || { echo "kafka is required: brew install kafka"; exit 1; }
	uv sync
	cd apps/web && pnpm install
	@mkdir -p $(LOG) $(RUN)
	@[ -d $(PGDATA) ] || { $(PG_BIN)/initdb -D $(PGDATA) -U $(DB_USER) --auth=trust --encoding=UTF8 >/dev/null && echo "initialized $(PGDATA)"; }
	@mkdir -p $(LOCAL)/kafka $(KAFKA_DATA)
	@sed "s|@KAFKA_DATA@|$(KAFKA_DATA)|" config/kafka/server.properties.template > $(KAFKA_CONF)
	@[ -f $(KAFKA_DATA)/meta.properties ] || { \
	  $(KAFKA_BIN)/kafka-storage format --standalone -t $$($(KAFKA_BIN)/kafka-storage random-uuid) -c $(KAFKA_CONF) >/dev/null && \
	  echo "initialized $(KAFKA_DATA)"; }
	@[ -f .env ] || { cp .env.example .env && echo "created .env from .env.example"; }
	@echo "setup complete — next: make start"

start: ## start postgres, kafka, migrate, then the stream, API, and web app in the background
	@mkdir -p $(LOG) $(RUN)
	@if $(PG_BIN)/pg_ctl -D $(PGDATA) status >/dev/null 2>&1; then \
	  echo "postgres: already running"; \
	elif lsof -ti :5432 >/dev/null 2>&1; then \
	  echo "error: another Postgres owns port 5432 — stop it or point RS_DATABASE_URL elsewhere"; exit 1; \
	else \
	  $(PG_BIN)/pg_ctl -D $(PGDATA) -l $(LOG)/postgres.log start >/dev/null; \
	fi
	@until $(PG_BIN)/pg_isready -h localhost -p 5432 -q; do sleep 1; done
	@$(PG_BIN)/psql -h localhost -U $(DB_USER) -d postgres -tAc \
	  "SELECT 1 FROM pg_database WHERE datname='$(DB_NAME)'" | grep -q 1 || \
	  $(PG_BIN)/createdb -h localhost -U $(DB_USER) $(DB_NAME)
	uv run scout db upgrade
	@$(MAKE) kafka-start
	@if [ -f $(RUN)/stream.pid ] && kill -0 $$(cat $(RUN)/stream.pid) 2>/dev/null; then \
	  echo "stream: already running"; \
	else \
	  nohup uv run scout stream serve >> $(LOG)/stream.log 2>&1 & echo $$! > $(RUN)/stream.pid; \
	fi
	@if [ -f $(RUN)/api.pid ] && kill -0 $$(cat $(RUN)/api.pid) 2>/dev/null; then \
	  echo "api: already running"; \
	else \
	  nohup uv run scout serve api >> $(LOG)/api.log 2>&1 & echo $$! > $(RUN)/api.pid; \
	fi
	@for i in $$(seq 1 60); do curl -sf http://127.0.0.1:8000/healthz >/dev/null && break; sleep 1; done; \
	  curl -sf http://127.0.0.1:8000/healthz >/dev/null || { echo "api did not come up — check: make logs"; exit 1; }
	@if [ -f $(RUN)/web.pid ] && kill -0 $$(cat $(RUN)/web.pid) 2>/dev/null; then \
	  echo "web: already running"; \
	else \
	  cd apps/web && { nohup ./node_modules/.bin/astro dev >> $(LOG)/web.log 2>&1 & echo $$! > $(RUN)/web.pid; }; \
	fi
	@echo
	@echo "  web       http://localhost:4321   (no sign-in — you are the local user)"
	@echo "  api docs  http://localhost:8000/docs"
	@echo
	@echo "  next: make seed   (chat needs 'ollama serve' + qwen2.5:3b-instruct, or a cloud key in .env)"

stop: ## stop the web app, API, stream, kafka, and postgres
	-@[ -f $(RUN)/web.pid ] && kill $$(cat $(RUN)/web.pid) 2>/dev/null; rm -f $(RUN)/web.pid
	-@[ -f $(RUN)/api.pid ] && kill $$(cat $(RUN)/api.pid) 2>/dev/null; rm -f $(RUN)/api.pid
	-@[ -f $(RUN)/stream.pid ] && kill $$(cat $(RUN)/stream.pid) 2>/dev/null; rm -f $(RUN)/stream.pid
	-@lsof -ti :4321 2>/dev/null | xargs kill -9 2>/dev/null; true
	-@lsof -ti :8000 2>/dev/null | xargs kill -9 2>/dev/null; true
	-@[ -f $(RUN)/kafka.pid ] && kill $$(cat $(RUN)/kafka.pid) 2>/dev/null; rm -f $(RUN)/kafka.pid
	-@lsof -ti :9092 2>/dev/null | xargs kill 2>/dev/null; true
	-@$(PG_BIN)/pg_ctl -D $(PGDATA) status >/dev/null 2>&1 && $(PG_BIN)/pg_ctl -D $(PGDATA) stop >/dev/null
	@echo "stopped"

status: ## show what is running
	@$(PG_BIN)/pg_ctl -D $(PGDATA) status >/dev/null 2>&1 && echo "postgres: up on :5432" || echo "postgres: stopped"
	@lsof -ti :8000 >/dev/null 2>&1 && echo "api: up on :8000" || echo "api: stopped"
	@lsof -ti :4321 >/dev/null 2>&1 && echo "web: up on :4321" || echo "web: stopped"
	@lsof -ti :9092 >/dev/null 2>&1 && echo "kafka: up on :9092" || echo "kafka: stopped"
	@[ -f $(RUN)/stream.pid ] && kill -0 $$(cat $(RUN)/stream.pid) 2>/dev/null && echo "stream: running" || echo "stream: stopped"

logs: ## tail the local service logs
	tail -f $(LOG)/*.log

seed: ## ingest ~25 recent cs.LG papers and index them
	uv run scout ingest --since $$(uv run python -c "from datetime import UTC, datetime, timedelta; print((datetime.now(UTC)-timedelta(days=7)).date())") --category cs.LG --max 25
	uv run scout index

digest: ## build and publish this week's digest (needs the LLM up)
	uv run scout digest

scheduler: ## run the refresh loop in the foreground (Ctrl-C to stop)
	uv run scout serve scheduler

kafka-start: ## start the kafka broker (KRaft single node) in the background
	@mkdir -p $(LOG) $(RUN)
	@[ -f $(KAFKA_CONF) ] || { echo "no kafka config — run: make setup"; exit 1; }
	@if [ -f $(RUN)/kafka.pid ] && kill -0 $$(cat $(RUN)/kafka.pid) 2>/dev/null; then \
	  echo "kafka: already running"; \
	elif lsof -ti :9092 >/dev/null 2>&1; then \
	  echo "error: another process owns port 9092 — stop it first"; exit 1; \
	else \
	  KAFKA_HEAP_OPTS="$(KAFKA_HEAP)" nohup $(KAFKA_BIN)/kafka-server-start $(KAFKA_CONF) >> $(LOG)/kafka.log 2>&1 & echo $$! > $(RUN)/kafka.pid; \
	fi
	@for i in $$(seq 1 30); do lsof -ti :9092 >/dev/null 2>&1 && break; sleep 1; done; \
	  lsof -ti :9092 >/dev/null 2>&1 || { echo "kafka did not come up — check: make logs"; exit 1; }
	@echo "kafka: up on :9092"

kafka-stop: ## stop the kafka broker
	-@[ -f $(RUN)/kafka.pid ] && kill $$(cat $(RUN)/kafka.pid) 2>/dev/null; rm -f $(RUN)/kafka.pid
	-@lsof -ti :9092 2>/dev/null | xargs kill 2>/dev/null; true
	@echo "kafka: stopped"

check: ## everything CI runs: lint, types, unit tests, web check + build
	uv run ruff check researchscout tests
	uv run ruff format --check researchscout tests
	uv run mypy researchscout
	uv run pytest -m "not integration"
	cd apps/web && pnpm astro check && pnpm build

clean: ## stop everything and delete .local (all Postgres data, logs, pids)
	@$(MAKE) stop
	rm -rf $(LOCAL)
	@echo "removed $(LOCAL)"
