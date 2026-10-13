#!/usr/bin/env bash
# Known-vulnerability audit of the locked dependencies (pip-audit + npm audit).
# Exits non-zero when something is found; `make build` treats this as a warning only.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"
require_venv
cd "$ROOT"
status=0
run_step "pip-audit (requirements.lock)" \
    "$VENV_BIN/pip-audit" --disable-pip --require-hashes -r requirements.lock || status=1
run_step "npm audit (ui, production deps)" \
    bash -c 'cd ui && npm audit --omit=dev --audit-level=moderate' || status=1
exit $status
