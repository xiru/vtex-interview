# Catalog consolidation — developer and operations entry points.
# Every target is a thin wrapper over scripts/*.sh or docker compose; run `make help`.

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE        := docker compose
LEGACY_SQLITE  ?= ./data/catalog.db
ARGS           ?=

.PHONY: help setup lint format typecheck test test-api test-ui test-unit coverage audit check \
        build smoke psqlmigrate up down logs bootstrap migrate import undo cli db-shell db-dump \
        db-reset hooks lock clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ----------------------------------------------------------------------------- development
setup: ## Create .venv (hash-verified deps), install UI deps, create .env
	@scripts/setup.sh

lint: ## ruff + format check, eslint + prettier, shellcheck (hadolint if installed)
	@scripts/lint.sh

format: ## Apply automatic lint fixes and formatting
	@scripts/lint.sh --fix

typecheck: ## mypy
	@scripts/typecheck.sh

test: ## All tests with coverage gates (starts a throwaway PostgreSQL)
	@scripts/test.sh all $(ARGS)

test-api: ## Python tests only, e.g. make test-api ARGS="-k undo -x"
	@scripts/test.sh api $(ARGS)

test-ui: ## UI tests only
	@scripts/test.sh ui

test-unit: ## Fast Python unit tests (no database, no docker)
	@scripts/test.sh unit $(ARGS)

coverage: ## Tests, then print where the HTML coverage reports are
	@scripts/test.sh all --open

audit: ## Known-vulnerability audit (pip-audit + npm audit)
	@scripts/audit.sh

check: ## lint + typecheck + tests (blocking) + audit (advisory)
	@scripts/check.sh

lock: ## Recompile the hash-pinned Python lockfiles
	.venv/bin/pip-compile -q --generate-hashes --allow-unsafe --strip-extras -o requirements.lock requirements.in
	.venv/bin/pip-compile -q --generate-hashes --allow-unsafe --strip-extras -o requirements-dev.lock requirements-dev.in

hooks: ## Install an optional git pre-commit hook (lint + unit tests)
	@scripts/install-hooks.sh

clean: ## Remove local build/test artifacts
	rm -rf .venv reports .coverage .pytest_cache .mypy_cache .ruff_cache src/*.egg-info \
		ui/node_modules ui/coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

# ----------------------------------------------------------------------------- build
build: ## Quality gates -> docker images -> smoke test (audit is advisory)
	@scripts/build.sh

smoke: ## End-to-end test of the built images (isolated compose project)
	@scripts/smoke.sh

# ----------------------------------------------------------------------------- run
psqlmigrate: ## One-time load of the legacy SQLite catalog into PostgreSQL (idempotent)
	@LEGACY_SQLITE=$(LEGACY_SQLITE) scripts/psqlmigrate.sh

up: ## Start postgres -> migrate -> api (:8000) -> ui (:8080); never builds images
	@docker image inspect catalog-api:latest catalog-ui:latest >/dev/null 2>&1 || \
		{ echo "images not found: run make build first"; exit 1; }
	$(COMPOSE) up -d --no-build --wait
	@echo "API: http://localhost:$${API_PORT:-8000}/api/v1/docs   UI: http://localhost:$${UI_PORT:-8080}"

down: ## Stop the stack (data is kept in the pgdata volume)
	$(COMPOSE) down

logs: ## Follow the stack logs
	$(COMPOSE) logs -f

bootstrap: build psqlmigrate up ## Fresh machine: build, load legacy catalog, start

migrate: ## Apply pending schema migrations
	$(COMPOSE) run --rm migrate

import: ## Import a JSON file from any path: make import FILE=/path/to/entries.json
	@test -n "$(FILE)" || { echo "usage: make import FILE=/path/to/file.json"; exit 2; }
	IMPORT_DIR="$$(cd "$$(dirname "$(FILE)")" && pwd)" \
		$(COMPOSE) run --rm cli import run "/imports/$$(basename "$(FILE)")" $(ARGS)

undo: ## Undo imports: make undo ARGS="--md5 <hash> [--seller NAME] [--yes]"
	$(COMPOSE) run --rm cli import undo $(ARGS)

cli: ## Any catalog command: make cli ARGS="import list --since 2d"
	$(COMPOSE) run --rm cli $(ARGS)

db-shell: ## psql as the read-only role
	$(COMPOSE) exec postgres psql -U catalog_reader -d catalog

db-dump: ## pg_dump to ./backups/
	@mkdir -p backups
	$(COMPOSE) exec -T postgres pg_dump -U catalog_owner -d catalog -Fc \
		> backups/catalog-$$(date +%Y%m%d-%H%M%S).dump
	@ls -1t backups | head -1

db-reset: ## DESTRUCTIVE: drop the database volume (make db-reset CONFIRM=yes)
	@test "$(CONFIRM)" = "yes" || { echo "refusing: run make db-reset CONFIRM=yes"; exit 2; }
	$(COMPOSE) down -v
