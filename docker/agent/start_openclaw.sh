#!/bin/bash

##############################################
# start_openclaw.sh - OpenClaw engine pod startup
#
# Per-engine startup script for --engine openclaw.
#
# Normally exec'd by start_service.sh (arguments parsed, credentials
# saved, ready marker reset, MARKER_FILE / ADAPTOR_PORT exported). Also
# runnable directly for recovery/debugging — it carries its own defaults
# for the inherited variables.
#
# Flow:
#   1. Write .adaptorEnv (openclaw). Also clear a stale .relayEnv left by
#      a previous claude_code pod, so a later manual
#      `supervisorctl start claude_relay` cannot pick up old credentials.
#   2. Wait for the supervisord socket
#   3. Start the engine program via supervisorctl
#   4. Wait for the engine /health endpoint
#   5. Write the ready marker and print status
##############################################

set -e

# --- Locate scripts directory ---

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source util.sh for logging
source "$SCRIPT_DIR/util.sh"

# Same log file as the dispatcher (and start_claude_code.sh) so one file
# holds the whole pod startup trace, whatever the engine.
LOG_FILE="/home/admin/logs/start_service.log"
set_log_file "$LOG_FILE"

# --- Inherited context (fallbacks for direct invocation) ---

MARKER_FILE="${MARKER_FILE:-/var/run/agentclaw/.starting_done}"
ADAPTOR_PORT="${ADAPTOR_PORT:-20003}"
ENGINE="openclaw"

section "start_openclaw.sh - openclaw engine startup"

# --- Step 1: Configure engine environment ---

section "Step 1: Configuring engine environment..."

ADAPTOR_ENV_FILE="/home/admin/.adaptorEnv"
cat > "$ADAPTOR_ENV_FILE" <<EOF
export ENGINE=$ENGINE
export CHAT_ENGINE=$ENGINE
EOF
success "Engine env file written to $ADAPTOR_ENV_FILE"

# No relay for openclaw; a stale .relayEnv from a previous claude_code pod
# would let a manual `supervisorctl start claude_relay` boot with old
# credentials.
rm -f /home/admin/.relayEnv

# --- Step 2: Wait for supervisord socket ---

section "Step 2: Waiting for supervisord..."

SUPERVISOR_SOCK="/var/run/supervisor.sock"
MAX_WAIT=30
waited=0
while [ ! -S "$SUPERVISOR_SOCK" ]; do
    if [ $waited -ge "$MAX_WAIT" ]; then
        fail "supervisord socket not ready after ${MAX_WAIT}s"
        echo "FAILED" > "$MARKER_FILE"
        exit 1
    fi
    sleep 1
    waited=$((waited + 1))
done
success "supervisord ready (waited ${waited}s)"

# --- Step 3: Start engine via supervisorctl ---

section "Step 3: Starting engine program..."

ENGINE_RUNNING=$(sudo /usr/local/bin/supervisorctl status engine 2>/dev/null | grep -c "RUNNING" || true)

if [ "$ENGINE_RUNNING" -gt 0 ]; then
    info "Engine is already running"
else
    info "Starting engine via supervisorctl..."
    if sudo /usr/local/bin/supervisorctl start engine; then
        success "Engine program started"
    else
        fail "Failed to start engine via supervisorctl"
        echo "FAILED" > "$MARKER_FILE"
        exit 1
    fi
fi
# The engine starts the openclaw program on demand (first bot session) via
# `sudo supervisorctl start openclaw` — nothing to do for it here.

# --- Step 4: Wait for engine /health endpoint ---

section "Step 4: Waiting for engine health (port $ADAPTOR_PORT)..."

HEALTH_CHECK_TIMEOUT=120
HEARTBEAT_INTERVAL=10
ADAPTOR_READY=0

for i in $(seq 1 "$HEALTH_CHECK_TIMEOUT"); do
    HEALTH_RESP=$(curl -s --max-time 3 "http://127.0.0.1:${ADAPTOR_PORT}/health" 2>/dev/null || true)
    if echo "$HEALTH_RESP" | grep -q '"status".*:.*"ok"'; then
        ADAPTOR_READY=1
        break
    fi
    if [ $((i % HEARTBEAT_INTERVAL)) -eq 0 ]; then
        info "  Still waiting for engine /health (${i}s / ${HEALTH_CHECK_TIMEOUT}s)..."
    fi
    sleep 1
done

if [ "$ADAPTOR_READY" -ne 1 ]; then
    warn "Engine health check not ready after ${HEALTH_CHECK_TIMEOUT}s"
    info "  Last response: $HEALTH_RESP"
else
    success "Engine health check passed"
fi

# --- Step 5: Write ready marker & print status ---

echo "SUCCEEDED" > "$MARKER_FILE"

section "Service startup completed"
info ""
info "Service Status:"
info "  - Engine:   Running (port $ADAPTOR_PORT, engine=$ENGINE)"
info "  - OpenClaw: On-demand (started by engine when a session is requested)"
info "  - Health:   http://127.0.0.1:${ADAPTOR_PORT}/health"
info ""
info "Log Files:"
info "  - Engine:   /home/admin/logs/engine_out.log"
info "  - OpenClaw: /home/admin/logs/openclaw_out.log"
info "  - Startup:  $LOG_FILE"
info ""
info "Credentials:"
info "  - Stored in: /home/admin/.credentials"
info "  - Engine:    $ENGINE"
section "====="
