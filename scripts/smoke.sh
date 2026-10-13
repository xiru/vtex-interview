#!/usr/bin/env bash
# End-to-end smoke test of the built images, as an isolated compose project with its own
# database volume and ports. Never touches the dev database; data/catalog.db is read-only.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"
cd "$ROOT"

export COMPOSE_PROJECT_NAME=catalog-smoke
export API_PORT="${SMOKE_API_PORT:-18000}" UI_PORT="${SMOKE_UI_PORT:-18080}"
export POSTGRES_PASSWORD=smoke CATALOG_OWNER_PASSWORD=smoke CATALOG_READER_PASSWORD=smoke
export LEGACY_SQLITE=./data/catalog.db IMPORT_DIR=./data
api="http://127.0.0.1:$API_PORT/api/v1"
ui="http://127.0.0.1:$UI_PORT"
failures=0

cleanup() {
    docker compose -p "$COMPOSE_PROJECT_NAME" -f compose.legacy-migration.yaml down -v \
        --remove-orphans >/dev/null 2>&1 || true
    docker compose -p "$COMPOSE_PROJECT_NAME" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

check() {
    local name="$1"
    shift
    if "$@"; then pass "$name"; else fail "$name"; failures=$((failures + 1)); fi
}
json() { curl -fsS "$1" | "$PY" -c "import json,sys; d=json.load(sys.stdin); print($2)" | tr -d '\r'; }

cleanup
info "legacy migration (compose.legacy-migration.yaml)"
docker compose -f compose.legacy-migration.yaml up -d --wait postgres
migration_output="$(docker compose -f compose.legacy-migration.yaml run --rm legacy-migrate)"
echo "$migration_output"
check "legacy load verified (975 products, 975 VTEX offers, 4 merges)" \
    bash -c "grep -q 'products: 975' <<<\"\$0\" && grep -q 'house_offers: 975' <<<\"\$0\" \
             && grep -q 'merges: 4' <<<\"\$0\"" "$migration_output"
check "second psqlmigrate is a no-op" bash -c \
    "docker compose -f compose.legacy-migration.yaml run --rm legacy-migrate | grep -q 'already migrated'"

info "starting the standard stack (compose.yaml)"
docker compose up -d --no-build --wait

import_output="$(docker compose run --rm cli import run /imports/ProductEntry.json)"
echo "$import_output" | head -20
check "import: 3 invalid records" grep -Eq 'invalid_records +3' <<<"$import_output"
md5="$(docker compose run --rm cli import hash /imports/ProductEntry.json | tr -d '\r')"

check "api health" test "$(json "$api/health" "d['status']")" = "ok"
check "api stats: 977 products, 21 sellers" test \
    "$(json "$api/stats" "d['products'], d['sellers']")" = "977 21"
check "api brand filter (Apple)" test "$(json "$api/products?brand=apple&limit=1" "d['total'] > 0")" = "True"
check "api product uuids are v7" test \
    "$(json "$api/products?limit=1000" "all(i['uuid'][14] == '7' for i in d['items'])")" = "True"
check "api category sellers" test "$(json "$api/categories/1/sellers" "d['total'] > 0")" = "True"
check "api rejects POST (405)" test "$(curl -s -o "$DEVNULL" -w '%{http_code}' -X POST "$api/products")" = "405"
for page in / /products /brands/30 /categories/1 /sellers/MegaStore /imports /merges /undos; do
    check "ui GET $page" curl -fsS -o "$DEVNULL" "$ui$page"
done
check "ui shows MegaStore catalog" bash -c "curl -fsS '$ui/sellers/MegaStore' | grep -q 'Smartphone Galaxy S23'"
check "ui rejects POST (405)" test "$(curl -s -o "$DEVNULL" -w '%{http_code}' -X POST "$ui/")" = "405"

docker compose run --rm cli import undo --md5 "$md5" --yes >/dev/null
check "undo by md5: back to 975 products" test "$(json "$api/stats" "d['products']")" = "975"
undo_id="$(docker compose run --rm cli undo list | awk 'NR==3 {print $1}')"
docker compose run --rm cli undo revert "$undo_id" --yes >/dev/null
check "revert of the undo: 977 products again" test "$(json "$api/stats" "d['products']")" = "977"

echo
if [[ $failures -gt 0 ]]; then
    fail "smoke test: $failures check(s) failed"
    exit 1
fi
pass "smoke test passed"
