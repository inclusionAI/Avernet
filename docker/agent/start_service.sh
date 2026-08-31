#!/bin/bash

##############################################
# start_service.sh - Pod startup dispatcher
#
# Reference: agentclaw-daas-scripts/bootstrapping/start_service.sh
#
# Thin dispatcher. It parses arguments, saves credentials, then checks
# --engine and execs the per-engine script; ALL engine-specific startup
# logic lives in the target script, not here:
#   openclaw    → start_openclaw.sh     (engine program only)
#   claude_code → start_claude_code.sh  (claude_relay + engine; requires
#                                      ANTHROPIC_* model credentials in
#                                      the pod env — see that script's
#                                      header for the contract)
#
# Invoked by the platform (docker exec) once supervisord is PID 1.
#
# Flow:
#   1. Parse arguments (token, engine, bot_id, stage, ...)
#   2. Save credentials
#   3. Check --engine and dispatch
#
# Usage:
#   start_service.sh \
#       --token <token> \
#       --client_id <client_id> \
#       --engine openclaw|claude_code  (default openclaw) \
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

# --- Ready marker (path exported for the per-engine scripts) ---

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

section "start_service.sh - pod startup dispatcher"

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

# --- Step 2: Check engine and dispatch ---

# Shared context for the per-engine scripts (they also carry their own
# fallbacks so they stay independently runnable for recovery/debugging).
export MARKER_FILE ADAPTOR_PORT

case "$ENGINE" in
    openclaw)
        info "Engine type: openclaw — dispatching to start_openclaw.sh"
        exec "$SCRIPT_DIR/start_openclaw.sh"
        ;;
    claude_code)
        info "Engine type: claude_code — dispatching to start_claude_code.sh"
        exec "$SCRIPT_DIR/start_claude_code.sh"
        ;;
    *)
        fail "Unknown engine: $ENGINE (supported: openclaw, claude_code)"
        echo "FAILED" > "$MARKER_FILE"
        exit 1
        ;;
esac
