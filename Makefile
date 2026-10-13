# Catalog consolidation — developer and operations entry points.
# Every target is a thin wrapper over scripts/*.sh or docker compose; run `make help`.

SHELL := /bin/bash
ifeq ($(OS),Windows_NT)
# Native Windows (GNU make from winget/choco/scoop): run recipes with Git for Windows' bash.
# make cannot use a SHELL path containing spaces, so take the 8.3 short path of the bash.exe
# that belongs to the git on PATH. Override with GIT_BASH=C:/path/without/spaces/bash.exe.
GIT_BASH ?= $(subst \,/,$(shell powershell -NoProfile -NonInteractive -Command "(New-Object -ComObject Scripting.FileSystemObject).GetFile((git --exec-path) + '/../../../bin/bash.exe').ShortPath"))
ifeq ($(GIT_BASH),)
$(error Git for Windows (Git Bash) not found: install it, or set GIT_BASH to its bin/bash.exe)
endif
ifneq ($(words $(GIT_BASH)),1)
$(error GIT_BASH contains spaces ($(GIT_BASH)): set GIT_BASH to a path without spaces)
endif
SHELL := $(GIT_BASH)
# Git Bash rewrites arguments that look like POSIX paths (/imports/...) for native programs.
export MSYS_NO_PATHCONV := 1
VENV_BIN := .venv/Scripts
else
VENV_BIN := .venv/bin
endif
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
	@$(SHELL) scripts/setup.sh

lint: ## ruff + format check, eslint + prettier, shellcheck (hadolint if installed)
	@$(SHELL) scripts/lint.sh

format: ## Apply automatic lint fixes and formatting
	@$(SHELL) scripts/lint.sh --fix

typecheck: ## mypy
	@$(SHELL) scripts/typecheck.sh

test: ## All tests with coverage gates (starts a throwaway PostgreSQL)
	@$(SHELL) scripts/test.sh all $(ARGS)

test-api: ## Python tests only, e.g. make test-api ARGS="-k undo -x"
	@$(SHELL) scripts/test.sh api $(ARGS)

test-ui: ## UI tests only
	@$(SHELL) scripts/test.sh ui

test-unit: ## Fast Python unit tests (no database, no docker)
	@$(SHELL) scripts/test.sh unit $(ARGS)

coverage: ## Tests, then print where the HTML coverage reports are
	@$(SHELL) scripts/test.sh all --open

audit: ## Known-vulnerability audit (pip-audit + npm audit)
	@$(SHELL) scripts/audit.sh

check: ## lint + typecheck + tests (blocking) + audit (advisory)
	@$(SHELL) scripts/check.sh

lock: ## Recompile the hash-pinned Python lockfiles
	$(VENV_BIN)/pip-compile -q --generate-hashes --allow-unsafe --strip-extras -o requirements.lock requirements.in
	$(VENV_BIN)/pip-compile -q --generate-hashes --allow-unsafe --strip-extras -o requirements-dev.lock requirements-dev.in

hooks: ## Install an optional git pre-commit hook (lint + unit tests)
	@$(SHELL) scripts/install-hooks.sh

clean: ## Remove local build/test artifacts
	rm -rf .venv reports .coverage .pytest_cache .mypy_cache .ruff_cache src/*.egg-info \
		ui/node_modules ui/coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

# ----------------------------------------------------------------------------- build
build: ## Quality gates -> docker images -> smoke test (audit is advisory)
	@$(SHELL) scripts/build.sh

smoke: ## End-to-end test of the built images (isolated compose project)
	@$(SHELL) scripts/smoke.sh

# ----------------------------------------------------------------------------- run
psqlmigrate: ## One-time load of the legacy SQLite catalog into PostgreSQL (idempotent)
	@LEGACY_SQLITE=$(LEGACY_SQLITE) $(SHELL) scripts/psqlmigrate.sh

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
	IMPORT_DIR="$$(cd "$$(dirname "$(FILE)")" && { pwd -W 2>/dev/null || pwd; })" \
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
