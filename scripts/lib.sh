#!/usr/bin/env bash
# Shared helpers for the developer scripts (sourced, not executed).

# Variables below are used by the scripts that source this file.
# shellcheck disable=SC2034
case "${OSTYPE:-}" in
    msys* | cygwin*) IS_WINDOWS=1 ;;
    *) IS_WINDOWS=0 ;;
esac
if [[ $IS_WINDOWS -eq 1 ]]; then
    # Git Bash on Windows: use C:/... paths (understood by bash and by native programs such as
    # docker, python and node) and stop bash from rewriting /container/paths arguments.
    export MSYS_NO_PATHCONV=1
    ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -W)"
    VENV="${VENV:-$ROOT/.venv}"
    VENV_BIN="$VENV/Scripts"
    PY="$VENV_BIN/python.exe"
    # Null device for native programs' file arguments (curl -o); bash redirections still use /dev/null.
    DEVNULL=NUL
else
    ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    VENV="${VENV:-$ROOT/.venv}"
    VENV_BIN="$VENV/bin"
    PY="$VENV_BIN/python"
    DEVNULL=/dev/null
    # Node.js installed with nvm is put on PATH by ~/.bashrc, i.e. only in interactive shells;
    # make, `wsl make ...`, editors and git hooks run these scripts non-interactively. Load
    # nvm's default node then (nvm.sh does not support `set -eu`, so relax it while sourcing).
    if ! command -v node >/dev/null 2>&1 && [[ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]]; then
        export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
        _opts=$-
        set +eu
        # shellcheck source=/dev/null
        source "$NVM_DIR/nvm.sh" >/dev/null 2>&1
        [[ $_opts == *e* ]] && set -e
        [[ $_opts == *u* ]] && set -u
        unset _opts
    fi
fi
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
