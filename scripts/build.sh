#!/usr/bin/env bash
# Full pipeline: quality gates -> docker images -> smoke test.
# Images are built only when lint, typecheck and tests (with coverage) pass. Audit findings
# are shown as a warning and do not block the build. There is no flag to skip the gates.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"
cd "$ROOT"

if ! "$ROOT/scripts/check.sh"; then
    fail "quality gates failed: docker images were NOT built"
    exit 1
fi
if [[ "$(cat "$REPORTS/.audit_warning" 2>/dev/null || echo 0)" == "1" ]]; then
    warn "================================================================"
    warn " dependency audit reported issues (see output above)"
    warn " building anyway: the audit is advisory for make build"
    warn "================================================================"
fi

tag="$(git rev-parse --short HEAD 2>/dev/null || echo dev)"
info "building images (tags: latest, $tag)"
docker compose build
docker tag catalog-api:latest "catalog-api:$tag"
docker tag catalog-ui:latest "catalog-ui:$tag"
pass "images built: catalog-api:$tag catalog-ui:$tag"

"$ROOT/scripts/smoke.sh"
