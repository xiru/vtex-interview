#!/usr/bin/env bash
# Create the local development environment: Python venv, UI dependencies and .env.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

# First Python >= 3.12 found: $PYTHON, then the newest usual names on Linux and Windows
# (py launcher). 3.12 is the default python3 of Ubuntu 24.04 LTS; the Docker image uses 3.14.
find_python() {
    local candidate
    local -a cmd
    for candidate in ${PYTHON:+"$PYTHON"} python3.14 python3.13 python3.12 python3 python "py -3"; do
        read -ra cmd <<<"$candidate"
        command -v "${cmd[0]}" >/dev/null 2>&1 || continue
        "${cmd[@]}" -c 'import sys
if sys.version_info[:2] < (3, 12): sys.exit(1)
print(sys.executable.replace("\\", "/"))' 2>/dev/null | tr -d '\r' && return 0
    done
    return 1
}
PYTHON="$(find_python)" || { fail "Python >= 3.12 not found (set PYTHON=/path/to/python3)"; exit 1; }
command -v node >/dev/null || { fail "node not found (Node.js 24 required)"; exit 1; }
node_major="$(node -p 'process.versions.node.split(".")[0]')"
[[ "$node_major" -ge 24 ]] || { fail "Node.js >= 24 required, found $(node --version)"; exit 1; }
# WSL appends the Windows PATH: without a Linux npm, the Windows one (/mnt/c/...) would be used.
if [[ $IS_WINDOWS -eq 0 && "$(command -v npm)" == /mnt/* ]]; then
    fail "npm resolves to the Windows install ($(command -v npm)): install Node.js/npm inside WSL"
    exit 1
fi
docker compose version >/dev/null 2>&1 || warn "docker compose not found: tests and builds need it"

info "Python virtualenv ($VENV, $("$PYTHON" --version | tr -d '\r'))"
if [[ ! -x "$PY" ]] && ! "$PYTHON" -m venv "$VENV"; then
    rm -rf "$VENV"
    fail "could not create the virtualenv (Debian/Ubuntu: sudo apt install python3-venv)"
    exit 1
fi
"$PY" -m pip install --quiet --require-hashes -r "$ROOT/requirements-dev.lock"
"$PY" -m pip install --quiet --no-deps --no-build-isolation -e "$ROOT"
pass "python dependencies installed (hash-verified)"

info "UI dependencies"
(cd "$ROOT/ui" && npm ci --ignore-scripts --no-audit --no-fund)
pass "ui dependencies installed"

if [[ ! -f "$ROOT/.env" ]]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    warn ".env created from .env.example: change the passwords before exposing the stack"
fi
pass "setup complete"
