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
#   1. Write .adaptorEnv (claude_code + relay URL/cwd contract) and
#      .relayEnv for the claude_relay supervisord program (600, admin).
#      No credentials here — see "Provider config" below.
#   2. Wait for the supervisord socket
#   3. Start the claude_relay program via supervisorctl and health-gate
#      it (GET /health → {"ok":true}) before the engine
#   4. Start the engine program via supervisorctl
#   5. Wait for engine /health, write the ready marker and print status
#
# Provider config is static except the model provider host: the image
# stages docker/agent/claude-settings.json at
# /opt/claude-settings.json.template (NOT under /home/admin — that path is
# NAS-mounted at pod start and shadows image content) and Step 1 copies it
# into ~/.claude/settings.json AFTER the mount, mount-wins, substituting the
# MODEL_PROVIDER_HOST placeholder from the pod env (same variable the
# entrypoint's openclaw.json rendering uses, same bare-host contract: no
# scheme or path — the template carries https:// and /apps/anthropic).
# Everything else — model glm-5.2, and the auth token as the literal
# placeholder "Bearer ${API-KEY}" — mirrors openclaw.json ("apiKey":
# "Bearer ${API-KEY}"): the gateway on the upstream side replaces the
# placeholder with the real key. Swap the whole provider scenario by
# mounting a file at the final path.
#
# Reads (pod env):
#   MODEL_PROVIDER_HOST          optional, defaults dashscope.aliyuncs.com
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

# Nothing to validate beyond MODEL_PROVIDER_HOST (bare host or empty — the
# default is applied at the copy step below): every other provider field —
# model, and the auth token (the literal placeholder "Bearer ${API-KEY}") —
# is static in the settings.json template baked into the image. The gateway
# on the upstream side replaces the placeholder with the real key, exactly
# like openclaw.json's "apiKey": "Bearer ${API-KEY}" placeholder.

# --- Step 1: Configure engine + relay environment ---

section "Step 1: Configuring engine + relay environment..."

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
EOF
chown admin:admin "$RELAY_ENV_FILE" 2>/dev/null || true
chmod 600 "$RELAY_ENV_FILE"
success "Relay env file written to $RELAY_ENV_FILE (mode 600)"
# --- settings.json: copy AFTER the NAS mount, like openclaw's config ---
# /home/admin is NAS-mounted at pod start, which shadows anything baked into
# the image there — so the image stages the file at
# /opt/claude-settings.json.template and we copy it in here, at service
# start, AFTER the mount is live. Mount-wins: a settings.json already
# present on the NAS (a deployment's own provider scenario) is kept
# untouched. The copy substitutes the MODEL_PROVIDER_HOST placeholder (sed on
# the bare variable name, same literal pattern as the entrypoint's
# openclaw.json _sub — NOT the token's ${...} placeholder style). Only "/"
# is escaped in the sed replacement; hosts must not carry sed metacharacters.
CLAUDE_SETTINGS_FILE="/home/admin/.claude/settings.json"
CLAUDE_SETTINGS_TEMPLATE="/opt/claude-settings.json.template"
if [ ! -f "$CLAUDE_SETTINGS_FILE" ]; then
    if [ -f "$CLAUDE_SETTINGS_TEMPLATE" ]; then
        cp "$CLAUDE_SETTINGS_TEMPLATE" "$CLAUDE_SETTINGS_FILE"
        # Same default as the entrypoint's openclaw.json MODEL_PROVIDER_HOST
        # rendering (avernet-entrypoint.sh): an un-injected pod env keeps
        # the shipped dashscope scenario. "Bearer ${API-KEY}" is untouched.
        MODEL_PROVIDER_HOST="${MODEL_PROVIDER_HOST:-dashscope.aliyuncs.com}"
        _esc_model_provider_host="${MODEL_PROVIDER_HOST//\//\\/}"
        sed -i "s/MODEL_PROVIDER_HOST/${_esc_model_provider_host}/g" "$CLAUDE_SETTINGS_FILE"
        chown admin:admin "$CLAUDE_SETTINGS_FILE" 2>/dev/null || true
        chmod 600 "$CLAUDE_SETTINGS_FILE"
        success "Claude settings.json staged from template to $CLAUDE_SETTINGS_FILE (model provider host: ${MODEL_PROVIDER_HOST})"
    else
        warn "No $CLAUDE_SETTINGS_FILE on the mount and no template in the image; claude/relay fall back to their defaults"
    fi
fi

# Point the gateway's model-provider loader at the settings.json above. The
# loader's whitelist (model-provider-settings.ts) matches exactly the keys
# that file carries, and the claude CLI itself picks the same file up
# natively through CLAUDE_CONFIG_DIR = RELAY_CLAUDE_CONFIG_DIR =
# /home/admin/.claude.
if [ -f "$CLAUDE_SETTINGS_FILE" ]; then
    echo "export RELAY_MODEL_SETTINGS_SOURCE=$CLAUDE_SETTINGS_FILE" >> "$RELAY_ENV_FILE"
fi
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

# --- Step 3: Start claude_relay via supervisorctl and health-gate it ---

section "Step 3: Starting claude_relay program (port $CLAUDE_RELAY_PORT)..."

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
