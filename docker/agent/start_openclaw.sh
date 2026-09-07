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
#      Then reset workspace/HEARTBEAT.md from the image template — openclaw
#      appends tasks to that file at runtime and the /home/admin mount makes
#      the runtime copy persist, so every startup must overwrite it.
#   2. Render ~/.openclaw/openclaw.json from the image template: install on
#      first boot, deep-merge into the NAS copy on later boots (image keys
#      land, user-only keys survive). Then patch allowPrivateNetwork for
#      private model hosts. Moved here from the container entrypoint —
#      openclaw-specific config does not belong in the engine-agnostic
#      image bootstrap.
#   3. Wait for the supervisord socket
#   4. Start the engine program via supervisorctl
#   5. Wait for the engine /health endpoint
#   6. Write the ready marker and print status
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

# Reset the OpenClaw workspace heartbeat file from the image template.
# openclaw reads and appends tasks to workspace/HEARTBEAT.md at runtime, and
# /home/admin is NAS-mounted, so the runtime copy survives pod restarts. This
# script runs after the mount (via docker exec start_service.sh) and before
# openclaw's first on-demand start, so copying here overwrites whatever the
# previous run generated. Unconditional copy — template-wins, the opposite
# of start_claude_code.sh's mount-wins claude-settings.json staging.
HEARTBEAT_TEMPLATE="/opt/config/HEARTBEAT.md"
HEARTBEAT_TARGET="/home/admin/.openclaw/workspace/HEARTBEAT.md"
if [ -f "$HEARTBEAT_TEMPLATE" ]; then
    mkdir -p "$(dirname "$HEARTBEAT_TARGET")"
    cp -f "$HEARTBEAT_TEMPLATE" "$HEARTBEAT_TARGET"
    success "Workspace HEARTBEAT.md reset from template"
else
    warn "Heartbeat template missing at $HEARTBEAT_TEMPLATE - keeping existing file"
fi

# --- Step 2: Generate and patch ~/.openclaw/openclaw.json ---

section "Step 2: Configuring openclaw provider config..."

# Moved here from avernet-entrypoint.sh (which keeps only engine-agnostic
# image bootstrap): openclaw.json rendering + the allowPrivateNetwork patch
# below are openclaw-specific. This script runs before the engine starts
# openclaw on demand, so the program still sees the config it needs.
CONFIG_DIR="/home/admin/.openclaw"
CONFIG_FILE="${CONFIG_DIR}/openclaw.json"
TEMPLATE_FILE="/opt/openclaw.json.template"

# This script always runs AS ADMIN (the platform launches the whole chain
# via `su admin -c 'nohup start_service.sh ...'`), so no su/sudo drop here:
# admin identity keeps the generated files admin-owned and gives the CLI a
# correct $HOME. su from admin would prompt for a password and fail
# non-interactively.

# Render the image template with the pod env into a fresh candidate, on
# EVERY startup — not only first boot. The candidate is either installed
# (first boot) or deep-merged into the NAS copy below: /home/admin outlives
# image upgrades, so without the merge, image-side key additions/value
# updates (new provider flags, new model entries, changed defaults) could
# never reach pods that always have a persistent openclaw.json.
RENDER_OUT=$(mktemp /tmp/openclaw-config.XXXXXXXX)
chmod 644 "${RENDER_OUT}"

_gen_config() {
    cp "${TEMPLATE_FILE}" "${RENDER_OUT}"

    _sub() {
        local val
        val="${!1:-UNSET}"
        val="${val//\//\\/}"
        sed -i "s/${2}/${val}/g" "${RENDER_OUT}"
    }

    _sub OPENCLAW_OPENAI_BASE_URL  OPENCLAW_OPENAI_BASE_URL
    _sub MODEL_PROVIDER_HOST       MODEL_PROVIDER_HOST
    _sub OPENCLAW_OPENAI_API_KEY   OPENCLAW_OPENAI_API_KEY
    _sub OPENCLAW_OPENAI_MODEL_ID  OPENCLAW_OPENAI_MODEL_ID
    _sub OPENCLAW_OPENAI_MODEL_NAME OPENCLAW_OPENAI_MODEL_NAME
    _sub OPENCLAW_GATEWAY_TOKEN    OPENCLAW_GATEWAY_TOKEN
    _sub BCS_URL                   BCS_URL
    _sub BCS_BOT_ID                BCS_BOT_ID
    _sub BCS_BOT_NAME              BCS_BOT_NAME

    if [ "${OPENCLAW_GATEWAY_TOKEN:-UNSET}" = "UNSET" ]; then
        sed -i '/"auth": {/{N;s/"mode": "token", "token": "UNSET"//' "${RENDER_OUT}" 2>/dev/null || true
    fi
}
# MODEL_PROVIDER_HOST: model provider host (bare host, no scheme or path —
# the template carries the https:// prefix and the provider-specific path
# suffix). Shared contract with start_claude_code.sh, which substitutes
# the same placeholder into the claude_code settings.json. The default
# keeps the shipped dashscope scenario when the pod env does not inject
# it and must NOT fall through to _sub's UNSET literal (https://UNSET/...).
MODEL_PROVIDER_HOST="${MODEL_PROVIDER_HOST:-dashscope.aliyuncs.com}"
_gen_config

if [ ! -f "${CONFIG_FILE}" ]; then
    # First boot (or the NAS copy was removed): install the rendered
    # candidate as the config.
    cp "${RENDER_OUT}" "${CONFIG_FILE}"
    success "OpenClaw config written to ${CONFIG_FILE}"
else
    info "Existing ${CONFIG_FILE} found (NAS-mounted)"
    if [ -x /usr/local/bin/merge-config ]; then
        # Deep-merge the freshly rendered template INTO the NAS copy,
        # restricted to the allowlist in /opt/openclaw.path (maintained as
        # docker/agent/openclaw.path in the repo): keys listed there take
        # the image (post-upgrade) value, everything the user file carries
        # outside the list survives untouched — "meta" is not listed, so
        # the CLI's own lastTouched* bookkeeping stays user-owned. A no-op
        # merge writes nothing; changes are atomic with a .bak snapshot,
        # see merge-config.py.
        if merge-config --base "${RENDER_OUT}" --target "${CONFIG_FILE}" \
                --keys /opt/openclaw.path; then
            success "OpenClaw config merged from image template into ${CONFIG_FILE}"
        else
            warn "merge-config failed for ${CONFIG_FILE} — keeping existing file (image key updates may be missing)"
        fi
    else
        warn "merge-config not installed; using existing ${CONFIG_FILE} as-is"
    fi
fi
rm -f "${RENDER_OUT}"

# Allow private-network model provider endpoints.
# MODEL_PROVIDER_HOST can point at internal hosts (e.g. *.inner.avernet.com).
# openclaw blocks requests to private networks on a provider unless
# request.allowPrivateNetwork is true, so lift the restriction for the
# openai-compatible provider. Deliberate: the provider URL is
# operator-controlled (image template + pod env), not bot-controlled, so the
# SSRF guard is not needed here. Runs on EVERY startup, not only at
# first-boot rendering, so NAS-persisted configs from older images are
# patched too — but written only when the value is not already true, so
# startups with nothing to do never rewrite the file. Best-effort — a CLI
# failure must not block startup (the restriction then surfaces as
# per-request model errors instead of a dead startup).
if [ -x /usr/local/bin/openclaw ] && [ -f "${CONFIG_FILE}" ]; then
    # Idempotence: read the current value and only write when it is not
    # exactly "true" (a plain set on every boot keeps rewriting the
    # NAS-persisted config). The CLI prints its banner on every invocation,
    # so compare the LAST line of stdout, whitespace-stripped; "Config path
    # not found" and CLI failures both read as not-true and fall through to
    # the set. Exit codes are swallowed — config get exits non-zero on
    # not-found.
    _allow_private=$(openclaw config get \
        models.providers.openai-compatible.request.allowPrivateNetwork \
        2>/dev/null | tail -n 1 | tr -d '[:space:]' || true)
    if [ "${_allow_private}" != "true" ]; then
        if openclaw config set \
            models.providers.openai-compatible.request.allowPrivateNetwork true; then
            success "openclaw: private-network model endpoints allowed (allowPrivateNetwork=true)"
        else
            warn "openclaw config set allowPrivateNetwork failed — private model hosts will be blocked"
        fi
    else
        info "openclaw: allowPrivateNetwork already true (skipping config set)"
    fi
fi

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
# The engine starts the openclaw program on demand (first bot session) via
# `sudo supervisorctl start openclaw` — nothing to do for it here.

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
