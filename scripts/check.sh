#!/usr/bin/env bash
# All quality gates. lint, typecheck and tests (with coverage) are blocking; the dependency
# audit is advisory (reported as WARN). Every step runs so all problems show up at once.
set -uo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

declare -a names results
blocking_failed=0
audit_failed=0

step() {
    local name="$1" kind="$2"
    shift 2
    if "$@"; then
        names+=("$name"); results+=("PASS")
    elif [[ "$kind" == "advisory" ]]; then
        names+=("$name"); results+=("WARN"); audit_failed=1
    else
        names+=("$name"); results+=("FAIL"); blocking_failed=1
    fi
}

step "lint" blocking "$ROOT/scripts/lint.sh"
step "typecheck" blocking "$ROOT/scripts/typecheck.sh"
step "tests + coverage" blocking "$ROOT/scripts/test.sh" all
step "audit (advisory)" advisory "$ROOT/scripts/audit.sh"

echo
info "summary"
for i in "${!names[@]}"; do
    case "${results[$i]}" in
        PASS) pass "${names[$i]}" ;;
        WARN) warn "${names[$i]}" ;;
        *) fail "${names[$i]}" ;;
    esac
done

# Let build.sh know about audit warnings without failing the gate.
mkdir -p "$REPORTS"
echo "$audit_failed" >"$REPORTS/.audit_warning"
exit $blocking_failed
