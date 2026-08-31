#!/bin/bash

##############################################
# start_claude_code.sh - Claude Code engine pod startup
#
# Per-engine startup script for --engine claude_code.
#
# Normally exec'd by start_service.sh (arguments parsed, credentials
# saved, ready marker reset, MARKER_FILE / ADAPTOR_PORT exported). Also
# runnable directly for recovery/debugging — it carries its own defaults
# for the inherited variables.
#
# Flow:
#   1. Validate ANTHROPIC_* model credentials (fail fast)
#   2. Write .adaptorEnv (claude_code + relay URL/cwd contract) and
#      .relayEnv for the claude_relay supervisord program (600, admin:
#      credentials must not leak into supervisord config or image layers)
#   3. Wait for the supervisord socket
#   4. Start the claude_relay program via supervisorctl and health-gate
#      it (GET /health → {"ok":true}) before the engine
#   5. Start the engine program via supervisorctl
#   6. Wait for engine /health, write the ready marker and print status
#
# Reads (pod env; env contract mirrors scripts/modules/claude_relays.sh
# — singlebox — against the same vendored gateway):
#   ANTHROPIC_BASE_URL          required, Anthropic-compatible Messages API URL
#   ANTHROPIC_AUTH_TOKEN or
#   ANTHROPIC_API_KEY           required (one of), upstream credential
#   ANTHROPIC_MODEL             required, model id
#   ANTHROPIC_SMALL_FAST_MODEL  optional, defaults to ANTHROPIC_MODEL (the
#                               upstream relay serves one model; without this
#                               claude requests haiku and the upstream 404s)
#   CLAUDE_RELAY_PORT           optional, defaults 18900
#   CLAUDE_RELAY_PERMISSION_MODE optional, defaults acceptEdits
#   CLAUDE_CODE_PATH            optional, defaults /usr/local/bin/claude
##############################################

set -e

# --- Locate scripts directory ---

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source util.sh for logging
source "$SCRIPT_DIR/util.sh"

# Same log file as the dispatcher (and start_openclaw.sh) so one file
# holds the whole pod startup trace, whatever the engine.
LOG_FILE="/home/admin/logs/start_service.log"
set_log_file "$LOG_FILE"

# --- Inherited context (fallbacks for direct invocation) ---

MARKER_FILE="${MARKER_FILE:-/var/run/agentclaw/.starting_done}"
ADAPTOR_PORT="${ADAPTOR_PORT:-20003}"
ENGINE="claude_code"

# --- claude_code relay settings ---

CLAUDE_RELAY_PORT="${CLAUDE_RELAY_PORT:-18900}"
CLAUDE_RELAY_PERMISSION_MODE="${CLAUDE_RELAY_PERMISSION_MODE:-acceptEdits}"
RELAY_STATE_DIR="/home/admin/.claude-relay"
RELAY_GATEWAY_DIR="/opt/engine/src/engine/community/claude_code_gateway"

section "start_claude_code.sh - claude_code engine startup"

# --- Step 1: Validate model provider credentials (fail fast) ---

MISSING_RELAY_ENV=""
[ -n "${ANTHROPIC_BASE_URL:-}" ] || MISSING_RELAY_ENV="ANTHROPIC_BASE_URL"
if [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    MISSING_RELAY_ENV="${MISSING_RELAY_ENV} ANTHROPIC_AUTH_TOKEN-or-ANTHROPIC_API_KEY"
fi
[ -n "${ANTHROPIC_MODEL:-}" ] || MISSING_RELAY_ENV="${MISSING_RELAY_ENV} ANTHROPIC_MODEL"
if [ -n "$MISSING_RELAY_ENV" ]; then
    fail "--engine claude_code requires: ${MISSING_RELAY_ENV}"
    info "  Claude Code needs an Anthropic-compatible Messages API endpoint."
    echo "FAILED" > "$MARKER_FILE"
    exit 1
fi

ANTHROPIC_SMALL_FAST_MODEL="${ANTHROPIC_SMALL_FAST_MODEL:-$ANTHROPIC_MODEL}"

# --- Step 2: Configure engine + relay environment ---

section "Step 2: Configuring engine + relay environment..."

ADAPTOR_ENV_FILE="/home/admin/.adaptorEnv"
cat > "$ADAPTOR_ENV_FILE" <<EOF
export ENGINE=$ENGINE
export CHAT_ENGINE=$ENGINE
EOF

# Engine-side contract (read by the engine process, not the relay):
# CLAUDE_CODE_RELAY_URL is the adapter's WS target (plugin default is
# ws://127.0.0.1:18900 — keep it explicit so CLAUDE_RELAY_PORT overrides
# stay in sync); CLAUDE_CODE_DEFAULT_CWD / RELAY_DEFAULT_CWD anchor the
# engine-side workspace resolution (same pattern as
# scripts/run_bcs_mixed_provider.sh).
cat >> "$ADAPTOR_ENV_FILE" <<EOF
export CLAUDE_CODE_RELAY_URL=ws://127.0.0.1:$CLAUDE_RELAY_PORT
export CLAUDE_CODE_DEFAULT_CWD=/home/admin/.openclaw/workspace
export RELAY_DEFAULT_CWD=/home/admin/.openclaw/workspace
EOF
success "Engine env file written to $ADAPTOR_ENV_FILE"

mkdir -p "$RELAY_STATE_DIR/data" "$RELAY_STATE_DIR/logs" \
         /home/admin/.claude /home/admin/.openclaw/workspace
chown -R admin:admin "$RELAY_STATE_DIR" /home/admin/.claude 2>/dev/null || true

RELAY_ENV_FILE="/home/admin/.relayEnv"
cat > "$RELAY_ENV_FILE" <<EOF
export PORT=$CLAUDE_RELAY_PORT
export CLAUDE_CODE_PATH=${CLAUDE_CODE_PATH:-/usr/local/bin/claude}
export RELAY_DATA_DIR=$RELAY_STATE_DIR/data
export RELAY_LOG_DIR=$RELAY_STATE_DIR/logs
export RELAY_CLAUDE_CONFIG_DIR=/home/admin/.claude
export RELAY_DEFAULT_CWD=/home/admin/.openclaw/workspace
export RELAY_DEFAULT_PERMISSION_MODE=$CLAUDE_RELAY_PERMISSION_MODE
export ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL
export ANTHROPIC_MODEL=$ANTHROPIC_MODEL
export ANTHROPIC_SMALL_FAST_MODEL=$ANTHROPIC_SMALL_FAST_MODEL
EOF
# Credential line: AUTH_TOKEN preferred (bearer), else API_KEY.
if [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
    echo "export ANTHROPIC_AUTH_TOKEN=$ANTHROPIC_AUTH_TOKEN" >> "$RELAY_ENV_FILE"
else
    echo "export ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" >> "$RELAY_ENV_FILE"
fi
chown admin:admin "$RELAY_ENV_FILE" 2>/dev/null || true
chmod 600 "$RELAY_ENV_FILE"
success "Relay env file written to $RELAY_ENV_FILE (mode 600)"

# --- Step 3: Wait for supervisord socket ---

section "Step 3: Waiting for supervisord..."

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

# --- Step 4: Start claude_relay via supervisorctl and health-gate it ---

section "Step 4: Starting claude_relay program (port $CLAUDE_RELAY_PORT)..."

[ -f "$RELAY_GATEWAY_DIR/dist/esm/server.js" ] || {
    fail "claude_code gateway missing: $RELAY_GATEWAY_DIR/dist/esm/server.js"
    echo "FAILED" > "$MARKER_FILE"
    exit 1
}

RELAY_RUNNING=$(sudo /usr/local/bin/supervisorctl status claude_relay 2>/dev/null | grep -c "RUNNING" || true)

if [ "$RELAY_RUNNING" -gt 0 ]; then
    info "claude_relay is already running"
else
    info "Starting claude_relay via supervisorctl..."
    if sudo /usr/local/bin/supervisorctl start claude_relay; then
        success "claude_relay program started"
    else
        fail "Failed to start claude_relay via supervisorctl"
        info "  Check /home/admin/logs/claude_relay_err.log"
        echo "FAILED" > "$MARKER_FILE"
        exit 1
    fi
fi

# Health-gate the relay before starting the engine: the engine's
# claude_code adapter connects to ws://127.0.0.1:${CLAUDE_RELAY_PORT} at
# session startup, so an unhealthy relay surfaces as a WS upgrade error
# downstream instead of a clear boot failure.
RELAY_HEALTH_TIMEOUT=60
RELAY_READY=0
for i in $(seq 1 "$RELAY_HEALTH_TIMEOUT"); do
    if curl -s --max-time 2 "http://127.0.0.1:${CLAUDE_RELAY_PORT}/health" 2>/dev/null \
        | jq -e '.ok == true' >/dev/null 2>&1; then
        RELAY_READY=1
        break
    fi
    sleep 1
done

if [ "$RELAY_READY" -ne 1 ]; then
    fail "claude_relay health check not ready after ${RELAY_HEALTH_TIMEOUT}s"
    info "  Check /home/admin/logs/claude_relay_out.log and claude_relay_err.log"
    echo "FAILED" > "$MARKER_FILE"
    exit 1
fi
success "claude_relay health check passed (http://127.0.0.1:${CLAUDE_RELAY_PORT}/health)"

# --- Step 5: Start engine via supervisorctl ---

section "Step 5: Starting engine program..."

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

# --- Step 6: Wait for engine /health endpoint ---

section "Step 6: Waiting for engine health (port $ADAPTOR_PORT)..."

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

# --- Step 7: Write ready marker & print status ---

echo "SUCCEEDED" > "$MARKER_FILE"

section "Service startup completed"
info ""
info "Service Status:"
info "  - Engine:   Running (port $ADAPTOR_PORT, engine=$ENGINE)"
info "  - Relay:    Running (ws://127.0.0.1:${CLAUDE_RELAY_PORT})"
info "  - Health:   http://127.0.0.1:${ADAPTOR_PORT}/health"
info "               http://127.0.0.1:${CLAUDE_RELAY_PORT}/health"
info ""
info "Log Files:"
info "  - Engine:   /home/admin/logs/engine_out.log"
info "  - Relay:    /home/admin/logs/claude_relay_out.log"
info "  - Startup:  $LOG_FILE"
info ""
info "Credentials:"
info "  - Stored in: /home/admin/.credentials"
info "  - Engine:    $ENGINE"
info "  - Relay env (600): $RELAY_ENV_FILE"
section "====="
