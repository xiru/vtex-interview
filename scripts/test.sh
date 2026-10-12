#!/usr/bin/env bash
# Run the test suites with coverage gates.
#   scripts/test.sh [all|api|ui|unit] [extra pytest args...]
# api/all start the throwaway PostgreSQL from compose.test.yaml (KEEP_TEST_DB=1 keeps it up).
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

suite="${1:-all}"
[[ $# -gt 0 ]] && shift
open_reports=0
if [[ "${1:-}" == "--open" ]]; then
    open_reports=1
    shift
fi
cd "$ROOT"

start_test_db() {
    info "starting test PostgreSQL (compose.test.yaml)"
    "${COMPOSE_TEST[@]}" up -d --wait --quiet-pull >/dev/null
    if [[ "${KEEP_TEST_DB:-0}" != "1" ]]; then
        trap '"${COMPOSE_TEST[@]}" down -v >/dev/null 2>&1 || true' EXIT
    fi
}

python_tests() {
    require_venv
    start_test_db
    mkdir -p "$REPORTS/python"
    run_step "pytest (unit + integration, coverage >= 85%)" \
        "$VENV/bin/pytest" \
        --cov --cov-report=term \
        --cov-report="xml:$REPORTS/python/coverage.xml" \
        --cov-report="html:$REPORTS/python/html" \
        --junitxml="$REPORTS/python/junit.xml" "$@"
    run_step "coverage gate: catalog.validation >= 95%" \
        "$VENV/bin/coverage" report --include='src/catalog/validation/*' --fail-under=95 \
        --skip-covered
}

ui_tests() {
    require_ui_deps
    run_step "ui tests (node --test + supertest, c8 coverage >= 85%)" \
        bash -c 'cd ui && npm test --silent'
}

case "$suite" in
    unit)
        require_venv
        run_step "pytest -m unit" "$VENV/bin/pytest" -m unit -q "$@"
        ;;
    api) python_tests "$@" ;;
    ui) ui_tests ;;
    all)
        python_tests "$@"
        ui_tests
        ;;
    *)
        fail "unknown suite '$suite' (use all, api, ui or unit)"
        exit 2
        ;;
esac

if [[ $open_reports -eq 1 ]]; then
    info "reports:"
    echo "  python: $REPORTS/python/html/index.html"
    echo "  ui:     $REPORTS/ui/html/index.html"
fi
