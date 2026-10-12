#!/usr/bin/env bash
# Static type check of the Python package (mypy, moderate configuration in pyproject.toml).
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"
require_venv
cd "$ROOT"
run_step "mypy" "$VENV/bin/mypy"
