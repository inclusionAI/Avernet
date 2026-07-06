#!/bin/bash
# ============================================================================
# Master-Slave Service Group E2E Test - Runner
#
# Usage:
#   bash run.sh           # Run full test
#   bash run.sh --hold    # Run test then hold for manual inspection
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

info() { echo -e "  ${CYAN}→${NC} $1"; }
pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }

HOLD=false
[ "${1:-}" = "--hold" ] && HOLD=true

# ── Setup ─────────────────────────────────────────────────────────────────

info "Running setup..."
SETUP_OUTPUT=$(bash "$SCRIPT_DIR/setup.sh")
eval "$SETUP_OUTPUT"
pass "Setup complete"

# ── Teardown on exit ──────────────────────────────────────────────────────

cleanup() {
    info "Running teardown..."
    BCS_PORT="$BCS_PORT" COORD_PORT="$COORD_PORT" DBA_PORT="$DBA_PORT" \
    BCS_PID_FILE="$BCS_PID_FILE" BOTS_DIR="$BOTS_DIR" \
    COORD_PID="$COORD_PID" DBA_PID="$DBA_PID" \
    bash "$SCRIPT_DIR/teardown.sh"
}
trap cleanup EXIT

# ── Export for Python test ────────────────────────────────────────────────

export BCS_URL BCS_WS_URL BCS_PORT
export COORD_UUID DBA_UUID
export COORD_TOKEN DBA_TOKEN
export LOG_DIR

info "BCS_URL=$BCS_URL"
info "Coordinator UUID=$COORD_UUID"
info "DBA UUID=$DBA_UUID"

# ── Run test ──────────────────────────────────────────────────────────────

echo ""
echo "=========================================="
echo "  Master-Slave Service Group E2E Test"
echo "=========================================="
echo ""

if python3 "$SCRIPT_DIR/test_master_slave.py"; then
    echo ""
    pass "All tests passed"
    RESULT=0
else
    echo ""
    fail "Tests failed"
    RESULT=1
fi

# ── Hold for inspection ──────────────────────────────────────────────────

if $HOLD; then
    echo ""
    info "Holding for manual inspection. Press Enter to teardown..."
    info "  BCS: $BCS_URL"
    info "  Logs: $LOG_DIR"
    read -r
fi

exit $RESULT
