#!/usr/bin/env bash
# Optional git pre-commit hook: lint + fast unit tests.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"
hook="$ROOT/.git/hooks/pre-commit"
cat >"$hook" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail
make lint test-unit
HOOK
chmod +x "$hook"
pass "pre-commit hook installed ($hook)"
