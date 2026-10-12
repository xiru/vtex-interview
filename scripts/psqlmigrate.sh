#!/usr/bin/env bash
# One-time migration of the legacy SQLite catalog into PostgreSQL (idempotent).
#   LEGACY_SQLITE=/path/to/catalog.db scripts/psqlmigrate.sh
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"
cd "$ROOT"

export LEGACY_SQLITE="${LEGACY_SQLITE:-./data/catalog.db}"
[[ -f "$LEGACY_SQLITE" ]] || { fail "legacy SQLite file not found: $LEGACY_SQLITE"; exit 1; }
docker image inspect catalog-api:latest >/dev/null 2>&1 || {
    fail "image catalog-api:latest not found: run make build first"
    exit 1
}
compose=(docker compose -f compose.legacy-migration.yaml)

# Leave PostgreSQL running afterwards if the stack was already up.
was_running="$("${compose[@]}" ps --status running --quiet postgres 2>/dev/null || true)"
info "starting PostgreSQL"
"${compose[@]}" up -d --wait postgres
info "migrating $LEGACY_SQLITE (mounted read-only) into PostgreSQL"
status=0
"${compose[@]}" run --rm legacy-migrate || status=$?
[[ -n "$was_running" ]] || "${compose[@]}" stop postgres >/dev/null
if [[ $status -ne 0 ]]; then
    fail "legacy migration failed (nothing was loaded)"
    exit $status
fi
pass "legacy migration done; start the stack with: make up"
