#!/bin/bash
# ============================================================================
# AI Workbench Test Environment - Setup & Teardown
#
# Usage:
#   ./test_workbench.sh setup     # Start BCS + bots
#   ./test_workbench.sh teardown  # Stop all processes
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BCS_PORT="${BCS_PORT:-21000}"
BCS_URL="http://localhost:${BCS_PORT}"
BCS_BIN="${PROJECT_ROOT}/target/debug/bcs"

OPENCLAW_SCRIPT="$SCRIPT_DIR/start_three_openclaw.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "  ${CYAN}→${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }

# ============================================================================
# BCS Management
# ============================================================================

start_bcs() {
    if curl -s "$BCS_URL/health" > /dev/null 2>&1; then
        pass "BCS already running at $BCS_URL"
        return 0
    fi

    info "Starting BCS on port $BCS_PORT..."

    local bots_dir="$PROJECT_ROOT/three_openclaw_test_dir"
    mkdir -p "$bots_dir/logs"

    BCS_DATA_DIR="$bots_dir" \
    MOLTIS_BCS_PORT="$BCS_PORT" \
    RUST_LOG="debug" \
    "$BCS_BIN" &> "$bots_dir/logs/bcs.log" &

    for i in {1..15}; do
        if curl -s "$BCS_URL/health" > /dev/null 2>&1; then
            pass "BCS started on port $BCS_PORT"
            return 0
        fi
        sleep 1
    done

    fail "BCS failed to start"
    [ -f "$bots_dir/logs/bcs.log" ] && tail -20 "$bots_dir/logs/bcs.log" >&2
    return 1
}

stop_bcs() {
    info "Stopping BCS..."
    local pids
    pids=$(lsof -ti :$BCS_PORT 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill 2>/dev/null || true
        sleep 1
        echo "$pids" | xargs kill -9 2>/dev/null || true
        pass "BCS stopped"
    else
        info "BCS not running"
    fi
}

# ============================================================================
# Setup & Teardown
# ============================================================================

setup() {
    echo ""
    echo "========================================"
    echo "  Setup AI Workbench Test Environment"
    echo "========================================"
    echo ""

    if [ ! -f "$BCS_BIN" ]; then
        fail "BCS binary not found: $BCS_BIN"
        info "Run: cargo build --package bcs"
        return 1
    fi

    start_bcs || return 1

    if [ -x "$OPENCLAW_SCRIPT" ]; then
        info "Starting OpenClaw bots..."
        $OPENCLAW_SCRIPT start || warn "Failed to start bots, they may already be running"

        info "Onboarding bots..."
        $OPENCLAW_SCRIPT onboard || warn "Onboarding failed"

        sleep 3
    else
        fail "start_three_openclaw.sh not found or not executable"
        return 1
    fi

    echo ""
    pass "Setup complete!"
    info "BCS URL: $BCS_URL"
    info "WebSocket URL: ws://localhost:${BCS_PORT}/ws"
    echo ""
}

teardown() {
    echo ""
    echo "========================================"
    echo "  Teardown AI Workbench Test Environment"
    echo "========================================"
    echo ""

    if [ -x "$OPENCLAW_SCRIPT" ]; then
        $OPENCLAW_SCRIPT stop || true
    fi

    stop_bcs

    pass "Teardown complete"
}

# ============================================================================
# Main
# ============================================================================

case "${1:-}" in
    setup)    setup ;;
    teardown) teardown ;;
    *)
        echo "Usage: $0 setup|teardown"
        exit 1
        ;;
esac
