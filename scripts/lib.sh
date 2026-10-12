#!/usr/bin/env bash
# Shared helpers for the developer scripts (sourced, not executed).

# Variables below are used by the scripts that source this file.
# shellcheck disable=SC2034
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$ROOT/.venv}"
PY="$VENV/bin/python"
REPORTS="$ROOT/reports"
COMPOSE_TEST=(docker compose -f "$ROOT/compose.test.yaml")

if [[ -t 1 ]]; then
    GREEN=$'\e[32m'; RED=$'\e[31m'; YELLOW=$'\e[33m'; BOLD=$'\e[1m'; RESET=$'\e[0m'
else
    GREEN=""; RED=""; YELLOW=""; BOLD=""; RESET=""
fi

info() { printf '%s==> %s%s\n' "$BOLD" "$*" "$RESET"; }
pass() { printf '%sPASS%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '%sWARN%s %s\n' "$YELLOW" "$RESET" "$*"; }
fail() { printf '%sFAIL%s %s\n' "$RED" "$RESET" "$*"; }
skip() { printf '%sSKIP%s %s\n' "$YELLOW" "$RESET" "$*"; }

require_venv() {
    if [[ ! -x "$PY" ]]; then
        fail "virtualenv not found at $VENV (run: make setup)"
        exit 1
    fi
}

require_ui_deps() {
    if [[ ! -d "$ROOT/ui/node_modules" ]]; then
        fail "ui/node_modules missing (run: make setup)"
        exit 1
    fi
}

# run_step NAME COMMAND...: runs a command, prints PASS/FAIL, returns its status.
run_step() {
    local name="$1"
    shift
    info "$name"
    if "$@"; then
        pass "$name"
        return 0
    fi
    fail "$name"
    return 1
}
