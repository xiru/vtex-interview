#!/usr/bin/env bash
# Create the local development environment: Python venv, UI dependencies and .env.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

PYTHON="${PYTHON:-python3.14}"

command -v "$PYTHON" >/dev/null || { fail "$PYTHON not found (Python 3.14 required)"; exit 1; }
command -v node >/dev/null || { fail "node not found (Node.js 24 required)"; exit 1; }
node_major="$(node -p 'process.versions.node.split(".")[0]')"
[[ "$node_major" -ge 24 ]] || { fail "Node.js >= 24 required, found $(node --version)"; exit 1; }
docker compose version >/dev/null 2>&1 || warn "docker compose not found: tests and builds need it"

info "Python virtualenv ($VENV)"
[[ -x "$PY" ]] || "$PYTHON" -m venv "$VENV"
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
