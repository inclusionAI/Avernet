#!/bin/bash
# ============================================================================
# Structured Routing E2E Test - Runner
#
# Orchestrates: build BCS → start BCS + 3 OpenClaw → run test → teardown.
#
# Usage:
#   bash scripts/test-structured-routing/run.sh                                # structured routing test
#   bash scripts/test-structured-routing/run.sh sender-routes                  # sender_routes test
#   bash scripts/test-structured-routing/run.sh session-manager-worker         # task dispatch session test
#   bash scripts/test-structured-routing/run.sh state-machine-runtime          # state-machine runtime YAML E2E
#   bash scripts/test-structured-routing/run.sh service-invoke-manager-worker  # Part B service-invocation E2E
#   bash scripts/test-structured-routing/run.sh all                            # all tests
#
# Prerequisites:
#   - openclaw CLI installed (/opt/homebrew/bin/openclaw)
#   - BCN plugin linked at /opt/homebrew/lib/node_modules/openclaw/extensions/bcs
#   - pip3 install websockets httpx
#   - LLM provider configured (antchat API key)
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_MODE="${1:-structured}"  # structured | sender-routes | session | session-manager-worker | state-machine-runtime | service-invoke-manager-worker | all

echo ""
echo "== Routing E2E Test (Real OpenClaw) — mode: $TEST_MODE =="
echo ""

# ── Setup ──────────────────────────────────────────────────────────────────

# Run setup.sh and capture KEY=VALUE lines
SETUP_OUTPUT=$(bash "$SCRIPT_DIR/setup.sh")

# Parse setup output into env vars
eval "$SETUP_OUTPUT"

export BCS_PORT BCS_URL BCS_WS_URL BCS_PID_FILE BOTS_DIR BCS_LOG LOG_DIR
export COORD_PID DBA_PID DEVOPS_PID
export COORD_UUID DBA_UUID DEVOPS_UUID COORD_TOKEN
export COORDINATOR_PROFILE DBA_PROFILE DEVOPS_PROFILE
export SERVICE_API_KEY SERVICE_GROUP_ID

# Ensure teardown on exit
cleanup() {
    echo ""
    echo "-- Teardown --"
    BCS_PID_FILE="${BCS_PID_FILE:-}" \
    BOTS_DIR="${BOTS_DIR:-}" \
    COORD_PID="${COORD_PID:-}" \
    DBA_PID="${DBA_PID:-}" \
    DEVOPS_PID="${DEVOPS_PID:-}" \
    COORDINATOR_PROFILE="${COORDINATOR_PROFILE:-}" \
    DBA_PROFILE="${DBA_PROFILE:-}" \
    DEVOPS_PROFILE="${DEVOPS_PROFILE:-}" \
    bash "$SCRIPT_DIR/teardown.sh"
}
trap cleanup EXIT

# ── Run tests ──────────────────────────────────────────────────────────────

echo ""
echo "-- Services Ready --"
echo ""
echo "  BCS:         http://127.0.0.1:${BCS_PORT}"
echo "  Coordinator: http://localhost:${COORD_PORT}  (${COORD_UUID})"
echo "  DBA:         http://localhost:${DBA_PORT}  (${DBA_UUID})"
echo "  DevOps:      http://localhost:${DEVOPS_PORT}  (${DEVOPS_UUID})"
echo "  Logs:        ${LOG_DIR}"
echo ""
echo "Press Enter to run tests (or Ctrl+C to stay in manual mode)..."
read -r _

echo ""
echo "-- Running Tests ($TEST_MODE) --"
echo ""

TEST_EXIT=0

if [ "$TEST_MODE" = "structured" ] || [ "$TEST_MODE" = "all" ]; then
    echo ">> Structured routing test"
    python3 "$SCRIPT_DIR/test_structured_routing.py" || TEST_EXIT=$?
fi

if [ "$TEST_MODE" = "sender-routes" ] || [ "$TEST_MODE" = "all" ]; then
    echo ""
    echo ">> Sender routes test"
    python3 "$SCRIPT_DIR/test_sender_routes.py" || TEST_EXIT=$?
fi

if [ "$TEST_MODE" = "session" ] || [ "$TEST_MODE" = "all" ]; then
    echo ""
    echo ">> Session-aware routing test (Task 14)"
    python3 "$SCRIPT_DIR/test_session_routing.py" || TEST_EXIT=$?
fi

if [ "$TEST_MODE" = "session-manager-worker" ] || [ "$TEST_MODE" = "all" ]; then
    echo ""
    echo ">> Master-Slave Session routing test (Task 14)"
    python3 "$SCRIPT_DIR/test_manager_worker_session.py" || TEST_EXIT=$?
fi

if [ "$TEST_MODE" = "state-machine-runtime" ] || [ "$TEST_MODE" = "all" ]; then
    echo ""
    echo ">> State-machine runtime YAML E2E"
    python3 "$SCRIPT_DIR/test_state_machine_runtime.py" || TEST_EXIT=$?
fi

if [ "$TEST_MODE" = "service-invoke-manager-worker" ] || [ "$TEST_MODE" = "all" ]; then
    echo ""
    echo ">> Service-invocation manager-worker E2E (Part B)"
    python3 "$SCRIPT_DIR/test_service_invoke_manager_worker.py" || TEST_EXIT=$?
fi

echo ""
if [ $TEST_EXIT -eq 0 ]; then
    echo "== All E2E tests passed =="
else
    echo "== Tests FAILED (exit code $TEST_EXIT) =="
fi

# ── Interactive hold ──────────────────────────────────────────────────────

echo ""
echo "-- Services still running --"
echo ""
echo "  BCS:         http://127.0.0.1:${BCS_PORT}"
echo "  Coordinator: http://localhost:${COORD_PORT}  (${COORD_UUID})  token: test_token_${COORDINATOR_PROFILE}"
echo "  DBA:         http://localhost:${DBA_PORT}  (${DBA_UUID})  token: test_token_${DBA_PROFILE}"
echo "  DevOps:      http://localhost:${DEVOPS_PORT}  (${DEVOPS_UUID})  token: test_token_${DEVOPS_PROFILE}"
echo "  Logs:        ${LOG_DIR}"
echo "  BCS Token (Coordinator): ${COORD_TOKEN}"
echo ""
echo "Press Enter to teardown and exit..."
read -r _
