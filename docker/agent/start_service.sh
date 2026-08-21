#!/bin/bash

##############################################
# start_service.sh - Simplified pod startup script
#
# Reference: agentclaw-daas-scripts/bootstrapping/start_service.sh
#
# This script orchestrates service startup for the Avernet
# Engine + OpenClaw pod.  It is invoked by the container
# entrypoint after supervisord is running as PID 1.
#
# Flow:
#   1. Parse arguments (token, engine, bot_id, stage, ...)
#   2. Save credentials
#   3. Configure engine environment
#   4. Start the engine program via supervisorctl
#   5. Wait for engine /health endpoint to respond
#   6. Write ready marker
#
# Usage:
#   start_service.sh \
#       --token <token> \
#       --client_id <client_id> \
#       --engine openclaw \
#       [--bot_id <bot_id>] \
#       [--stage <stage>] \
#       [--owner_id <owner_id>]
##############################################

set -e

# --- Locate scripts directory ---

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source util.sh for logging
source "$SCRIPT_DIR/util.sh"

# Set log file
LOG_FILE="/home/admin/logs/start_service.log"
set_log_file "$LOG_FILE"

# --- Ready marker ---

MARKER_DIR="/var/run/agentclaw"
MARKER_FILE="$MARKER_DIR/.starting_done"
mkdir -p "$MARKER_DIR" 2>/dev/null || true
rm -f "$MARKER_FILE"

# --- Defaults ---

TOKEN=""
CLIENT_ID=""
OWNER_ID=""
BOT_ID=""
STAGE=""
ENGINE="openclaw"
ADAPTOR_PORT="${ADAPTOR_PORT:-20003}"

# --- Parse arguments ---

while [[ $# -gt 0 ]]; do
    case $1 in
        --token)
            [ $# -ge 2 ] || { warn "--token value missing"; shift; continue; }
            TOKEN="$2"; shift 2 ;;
        --client_id)
            [ $# -ge 2 ] || { warn "--client_id value missing"; shift; continue; }
            CLIENT_ID="$2"; shift 2 ;;
        --owner_id)
            [ $# -ge 2 ] || { warn "--owner_id value missing"; shift; continue; }
            OWNER_ID="$2"; shift 2 ;;
        --bot_id)
            [ $# -ge 2 ] || { warn "--bot_id value missing"; shift; continue; }
            BOT_ID="$2"; shift 2 ;;
        --stage)
            [ $# -ge 2 ] || { warn "--stage value missing"; shift; continue; }
            STAGE="$2"; shift 2 ;;
        --engine)
            [ $# -ge 2 ] || { warn "--engine value missing"; shift; continue; }
            ENGINE="$2"; shift 2 ;;
        *)
            warn "Unknown parameter $1 - ignored"
            shift ;;
    esac
done

section "start_service.sh - pod startup"

# --- Step 1: Save credentials ---

section "Step 1: Saving credentials..."

CREDENTIALS_FILE="/home/admin/.credentials"
mkdir -p /home/admin/.config
cat > "$CREDENTIALS_FILE" <<EOF
TOKEN=$TOKEN
CLIENT_ID=$CLIENT_ID
OWNER_ID=$OWNER_ID
BOT_ID=$BOT_ID
STAGE=$STAGE
ENGINE=$ENGINE
EOF
chmod 600 "$CREDENTIALS_FILE"
success "Credentials saved to $CREDENTIALS_FILE"

# --- Step 2: Configure engine environment ---

section "Step 2: Configuring engine environment..."

ADAPTOR_ENV_FILE="/home/admin/.adaptorEnv"
cat > "$ADAPTOR_ENV_FILE" <<EOF
export ENGINE=$ENGINE
export CHAT_ENGINE=$ENGINE
EOF
success "Engine env file written to $ADAPTOR_ENV_FILE"

# Export for this script's scope
export TOKEN CLIENT_ID OWNER_ID BOT_ID STAGE ENGINE CHAT_ENGINE="$ENGINE"

# --- Step 3: Wait for supervisord socket ---

section "Step 3: Waiting for supervisord..."

SUPERVISOR_SOCK="/var/run/supervisor.sock"
MAX_WAIT=30
waited=0
while [ ! -S "$SUPERVISOR_SOCK" ]; do
    if [ $waited -ge $MAX_WAIT ]; then
        fail "supervisord socket not ready after ${MAX_WAIT}s"
        echo "FAILED" > "$MARKER_FILE"
        exit 1
    fi
    sleep 1
    waited=$((waited + 1))
done
success "supervisord ready (waited ${waited}s)"

# --- Step 4: Start engine via supervisorctl ---

section "Step 4: Starting engine program..."

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

# --- Step 5: Wait for engine /health endpoint ---

section "Step 5: Waiting for engine health (port $ADAPTOR_PORT)..."

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

# --- Step 6: Write ready marker & print status ---

echo "SUCCEEDED" > "$MARKER_FILE"

section "Service startup completed"
info ""
info "Service Status:"
info "  - Engine:   Running (port $ADAPTOR_PORT)"
info "  - OpenClaw: On-demand (started by engine when a session is requested)"
info "  - Health:   http://127.0.0.1:${ADAPTOR_PORT}/health"
info ""
info "Log Files:"
info "  - Engine:   /home/admin/logs/engine_out.log"
info "  - OpenClaw: /home/admin/logs/openclaw_out.log"
info "  - Startup:  $LOG_FILE"
info ""
info "Credentials:"
info "  - Stored in: $CREDENTIALS_FILE"
info "  - Engine:    $ENGINE"
section "====="
