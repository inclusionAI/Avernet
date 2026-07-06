#!/bin/bash
# ============================================================================
# Structured Routing E2E Test - Teardown
#
# Stops BCS + OpenClaw instances and cleans up temp/profile directories.
# Works both with env vars from setup.sh and standalone (kills by port).
# ============================================================================

set -euo pipefail

GREEN='\033[0;32m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }

# ── Kill by port (always works, even without env vars) ────────────────────

kill_by_port() {
    local port="$1"
    local label="$2"
    local pids
    pids=$(lsof -ti :"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill 2>/dev/null || true
        sleep 0.5
        pids=$(lsof -ti :"$port" 2>/dev/null || true)
        if [ -n "$pids" ]; then
            echo "$pids" | xargs kill -9 2>/dev/null || true
        fi
        pass "$label stopped (port $port)"
    fi
}

BCS_PORT="${BCS_PORT:-21000}"
COORD_PORT="${COORD_PORT:-21200}"
DBA_PORT="${DBA_PORT:-21300}"
DEVOPS_PORT="${DEVOPS_PORT:-21400}"

kill_by_port "$BCS_PORT" "BCS"
kill_by_port "$COORD_PORT" "Coordinator"
kill_by_port "$DBA_PORT" "DBA"
kill_by_port "$DEVOPS_PORT" "DevOps"

# ── Stop by PID (if env vars available) ───────────────────────────────────

if [ -n "${BCS_PID_FILE:-}" ] && [ -f "$BCS_PID_FILE" ]; then
    BCS_PID=$(cat "$BCS_PID_FILE")
    kill "$BCS_PID" 2>/dev/null || true
    rm -f "$BCS_PID_FILE"
fi

for pid_var in COORD_PID DBA_PID DEVOPS_PID; do
    pid="${!pid_var:-}"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        sleep 0.3
        kill -9 "$pid" 2>/dev/null || true
    fi
done

# ── Clean up profile directories ──────────────────────────────────────────

for profile in bcs_test_coordinator bcs_test_dba bcs_test_devops; do
    if [ -d "$HOME/.openclaw-${profile}" ]; then
        rm -rf "$HOME/.openclaw-${profile}"
        pass "Removed profile: $profile"
    fi
done

# ── Copy logs before cleanup ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAST_LOGS="$SCRIPT_DIR/.last-run-logs"

if [ -n "${BOTS_DIR:-}" ] && [ -d "$BOTS_DIR/logs" ]; then
    rm -rf "$LAST_LOGS"
    cp -r "$BOTS_DIR/logs" "$LAST_LOGS"
    pass "Logs saved to $LAST_LOGS"
fi

# ── Clean up temp directory ───────────────────────────────────────────────

if [ -n "${BOTS_DIR:-}" ] && [ -d "$BOTS_DIR" ]; then
    rm -rf "$BOTS_DIR"
    pass "Cleaned up $BOTS_DIR"
fi

pass "Teardown complete"
