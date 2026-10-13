#!/usr/bin/env bash
# Lint everything. With --fix, apply automatic fixes/formatting instead of only checking.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"
require_venv
require_ui_deps

fix=0
[[ "${1:-}" == "--fix" ]] && fix=1
status=0

cd "$ROOT"
if [[ $fix -eq 1 ]]; then
    run_step "ruff fix" "$VENV_BIN/ruff" check --fix src tests || status=1
    run_step "ruff format" "$VENV_BIN/ruff" format src tests || status=1
    run_step "eslint fix" bash -c 'cd ui && npx --no-install eslint --fix .' || status=1
    run_step "prettier write" bash -c 'cd ui && npx --no-install prettier --write .' || status=1
else
    run_step "ruff (PEP 8, pyflakes, imports, bugbear, pyupgrade)" "$VENV_BIN/ruff" check src tests || status=1
    run_step "ruff format --check" "$VENV_BIN/ruff" format --check src tests || status=1
    run_step "eslint (ui)" bash -c 'cd ui && npx --no-install eslint .' || status=1
    run_step "prettier --check (ui)" bash -c 'cd ui && npx --no-install prettier --check .' || status=1
fi

if command -v shellcheck >/dev/null; then
    run_step "shellcheck" shellcheck -x scripts/*.sh docker/postgres/initdb/*.sh || status=1
else
    skip "shellcheck (not installed)"
fi
if command -v hadolint >/dev/null; then
    run_step "hadolint" hadolint Dockerfile ui/Dockerfile || status=1
else
    skip "hadolint (not installed; optional)"
fi
exit $status
