#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${ROOT}/singlebox/qwenwork.sh"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT
CALLS="${TEST_ROOT}/calls"
export DEP_DIR="${TEST_ROOT}"
unset MOLTIS_BCS_CONFIG
main() { printf '%s\n' "$*" >> "$CALLS"; }
mock_python() {
    case "${2:-}" in
        configured) return 1 ;;
        *) return 0 ;;
    esac
}
QWENWORK_PYTHON=mock_python
qwenwork_main start >/dev/null
expected=$'--standalone --bcs-env local start backend bcs gateway\n--standalone --bcs-env local restart bcs\n--standalone --bcs-env local restart frontend'
[ "$(cat "$CALLS")" = "$expected" ]
[ "$FRONTEND_VARIANT" = nextgen ]
[ "$QWENWORK_PROVIDER_SLUG" = local ]
: > "$CALLS"
qwenwork_main stop >/dev/null
[ "$(cat "$CALLS")" = '--standalone --bcs-env local stop frontend gateway bcs backend' ]
if qwenwork_main invalid >/dev/null 2>&1; then exit 1; fi
echo 'PASS: QwenWork minimal stack ordering and scope'
