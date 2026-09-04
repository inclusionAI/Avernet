#!/usr/bin/env bash
#
# BCS Bot one-click installation script
# Usage: curl https://${bcs-endpoint}/install.sh | bash -s -- --token <human-token> [--bot-name <name>]
#

set -e  # Exit immediately on error

# ============================================================================
# Configuration constants
# ============================================================================

BCS_ENDPOINT="${BCS_ENDPOINT:-http://127.0.0.1:21000}"
BCN_PLUGIN_PACKAGE="${BCN_PLUGIN_PACKAGE:-@avernet-plugin/openclaw-channel-bcn}"
BCN_PLUGIN_VERSION="${BCN_PLUGIN_VERSION:-latest}"
BCN_PLUGIN="${BCN_PLUGIN_PACKAGE}@${BCN_PLUGIN_VERSION}"
BCN_PLUGIN_NAME="openclaw-channel-bcn"
RETRY_COUNT=3
RETRY_DELAY=2
GATEWAY_RESTART_TIMEOUT=30

# ============================================================================
# Colored output
# ============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# ============================================================================
# Plugin detection
# ============================================================================

# `openclaw plugins list` renders a narrow table that wraps long plugin IDs
# across multiple lines, so a plain grep for the full ID never matches. Use
# the machine-readable --json output and match the plugin by its id field.
bcn_plugin_is_listed() {
    openclaw plugins list --json 2>/dev/null \
        | grep '"id"' \
        | grep -Fq "$BCN_PLUGIN_NAME"
}

# ============================================================================
# Argument parsing
# ============================================================================

HUMAN_TOKEN=""
BOT_NAME=""
BCS_ENDPOINT_EXPLICIT=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --token)
            HUMAN_TOKEN="$2"
            shift 2
            ;;
        --bot-name)
            BOT_NAME="$2"
            shift 2
            ;;
        --bcs-endpoint)
            BCS_ENDPOINT="$2"
            BCS_ENDPOINT_EXPLICIT=true
            shift 2
            ;;
        --help)
            cat <<EOF
BCS Bot one-click installation script

Usage:
  curl https://\${bcs-endpoint}/install.sh | bash -s -- --token <human-token> [options]

Required arguments:
  --token <token>           Human Token (obtained from the BCS Web Portal)

Optional arguments:
  --bot-name <name>         Bot display name (defaults to a prompt)
  --bcs-endpoint <url>      BCS server address (default: http://127.0.0.1:21000)
  --help                    Show this help message

Examples:
  # Interactive installation (will prompt for bot-name)
  curl https://bcs.example.com/install.sh | bash -s -- --token human_abc123

  # Non-interactive installation
  curl https://bcs.example.com/install.sh | bash -s -- --token human_abc123 --bot-name "MyAssistant"

EOF
            exit 0
            ;;
        *)
            log_error "Unknown argument: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# ============================================================================
# Environment check
# ============================================================================

log_info "Checking environment..."

# Check required tools
for cmd in curl jq; do
    if ! command -v $cmd &> /dev/null; then
        log_error "Missing required tool: $cmd"
        log_info "Install it and retry: sudo apt-get install $cmd (Debian/Ubuntu) or brew install $cmd (macOS)"
        exit 1
    fi
done

# Check OpenClaw CLI
if ! command -v openclaw &> /dev/null; then
    log_error "openclaw command not found"
    log_info "Please install OpenClaw first: https://docs.openclaw.ai/installation"
    exit 1
fi

# Check token
if [ -z "$HUMAN_TOKEN" ]; then
    log_error "Missing required argument: --token"
    log_info "Obtain a Human Token from the BCS Web Portal and retry"
    echo ""
    echo "Example: curl ${BCS_ENDPOINT}/install.sh | bash -s -- --token human_abc123"
    exit 1
fi

# Resolve OPENCLAW_WORKSPACE
if [ -z "$OPENCLAW_WORKSPACE" ]; then
    OPENCLAW_WORKSPACE="${HOME}/.openclaw"
    log_warn "OPENCLAW_WORKSPACE environment variable not set, using default: ${OPENCLAW_WORKSPACE}"
fi

derive_bcs_websocket_url() {
    # Derive the WebSocket URL the plugin connects to from the HTTP endpoint.
    #   http(s)://<host>:<port>  ->  ws(s)://<normalized-host>:<port>/ws/bot
    # localhost is normalized to 127.0.0.1 so the URL matches the documented
    # local-dev form (ws://127.0.0.1:21000/ws/bot).
    local scheme="ws"
    local host_port="$BCS_ENDPOINT"

    case "$BCS_ENDPOINT" in
        https://*)
            scheme="wss"
            host_port="${BCS_ENDPOINT#https://}"
            ;;
        http://*)
            scheme="ws"
            host_port="${BCS_ENDPOINT#http://}"
            ;;
        *)
            log_error "Invalid BCS_ENDPOINT format (must start with http:// or https://): ${BCS_ENDPOINT}" >&2
            exit 1
            ;;
    esac

    host_port="${host_port//localhost/127.0.0.1}"
    host_port="${host_port%/}"
    printf '%s://%s/ws/bot' "$scheme" "$host_port"
}

write_openclaw_bcs_config() {
    local config_file="${OPENCLAW_WORKSPACE}/openclaw.json"
    local tmp_file
    tmp_file="$(mktemp)"

    mkdir -p "$OPENCLAW_WORKSPACE"

    if [ -f "$config_file" ]; then
        if ! jq --arg bcs_url "$BCS_URL" --arg force_bcs_url "$BCS_ENDPOINT_EXPLICIT" '
            .channels = (.channels // {})
            | .channels.bcs = (.channels.bcs // {})
            | .channels.bcs.enabled = true
            | if (($force_bcs_url == "true") or (((.channels.bcs.bcsUrl // "") | length) == 0)) then
                .channels.bcs.bcsUrl = $bcs_url
              else
                .
              end
            | if (.channels.bcs.heartbeatIntervalMs == null or .channels.bcs.heartbeatIntervalMs == 60000) then
                .channels.bcs.heartbeatIntervalMs = 30000
              else
                .
              end
        ' "$config_file" > "$tmp_file"; then
            rm -f "$tmp_file"
            log_error "Failed to update OpenClaw config: ${config_file}"
            exit 1
        fi
    else
        jq -n --arg bcs_url "$BCS_URL" '{
            channels: {
                bcs: {
                    enabled: true,
                    bcsUrl: $bcs_url,
                    heartbeatIntervalMs: 30000
                }
            }
        }' > "$tmp_file"
    fi

    mv "$tmp_file" "$config_file"
    local effective_bcs_url
    effective_bcs_url="$(jq -r '.channels.bcs.bcsUrl // empty' "$config_file" 2>/dev/null || true)"
    log_success "OpenClaw BCS config written to: ${config_file}"
    if [ "$effective_bcs_url" = "$BCS_URL" ]; then
        log_info "  channels.bcs.bcsUrl: ${BCS_URL}"
    else
        log_info "  channels.bcs.bcsUrl preserved: ${effective_bcs_url}"
    fi
}

BCS_URL="$(derive_bcs_websocket_url)"

log_success "Environment check passed"
echo ""

# ============================================================================
# Check existing credentials
# ============================================================================

BCS_DIR="${OPENCLAW_WORKSPACE}/.bcs"
SESSION_FILE="${BCS_DIR}/session.json"
SKIP_REGISTER=false

if [ -f "$SESSION_FILE" ]; then
    EXISTING_UUID=$(jq -r '.bot_uuid // empty' "$SESSION_FILE" 2>/dev/null)
    EXISTING_TOKEN=$(jq -r '.token // empty' "$SESSION_FILE" 2>/dev/null)

    if [ -n "$EXISTING_UUID" ] && [ -n "$EXISTING_TOKEN" ]; then
        log_warn "Existing BCS credentials detected:"
        log_info "  Bot UUID: ${EXISTING_UUID}"
        log_info "  Credential file: ${SESSION_FILE}"
        echo ""
        echo -n "Reuse existing credentials? [Y/n] "
        read -r REUSE_CHOICE

        if [ -z "$REUSE_CHOICE" ] || [[ "$REUSE_CHOICE" =~ ^[Yy] ]]; then
            log_info "Reusing existing credentials, skipping registration"
            BOT_UUID="$EXISTING_UUID"
            BOT_TOKEN="$EXISTING_TOKEN"
            SKIP_REGISTER=true
        else
            log_info "Will re-register and overwrite existing credentials"
        fi
        echo ""
    fi
fi

# ============================================================================
# Get Bot name (only needed for new registration)
# ============================================================================

if [ "$SKIP_REGISTER" = false ]; then
    if [ -z "$BOT_NAME" ]; then
        log_info "Enter a Bot name (2-64 characters; letters, digits, underscore, hyphen, Chinese supported):"
        read -r BOT_NAME

        # Basic validation
        if [ -z "$BOT_NAME" ]; then
            log_error "Bot name cannot be empty"
            exit 1
        fi

        # Length check
        name_length=${#BOT_NAME}
        if [ $name_length -lt 2 ] || [ $name_length -gt 64 ]; then
            log_error "Bot name length must be between 2-64 characters (current: ${name_length})"
            exit 1
        fi
    fi

    log_info "Bot name: ${BOT_NAME}"
    echo ""
fi

# ============================================================================
# Register Bot
# ============================================================================

if [ "$SKIP_REGISTER" = false ]; then
    log_info "Registering Bot with the BCS server..."

    # URL-encode the Bot name
    BOT_NAME_ENCODED=$(printf %s "$BOT_NAME" | jq -sRr @uri)

    # Build the registration URL
    REGISTER_URL="${BCS_ENDPOINT}/register?token=${HUMAN_TOKEN}&bot-name=${BOT_NAME_ENCODED}"

    # Send the registration request (with retries)
    REGISTER_RESPONSE=""
    for i in $(seq 1 $RETRY_COUNT); do
        log_info "Attempting registration... (${i}/${RETRY_COUNT})"

        HTTP_CODE=$(curl -s -w "%{http_code}" -o /tmp/bcs_register_response.json -X POST "$REGISTER_URL")

        if [ "$HTTP_CODE" = "200" ]; then
            REGISTER_RESPONSE=$(cat /tmp/bcs_register_response.json)
            log_success "Registration succeeded!"
            break
        else
            ERROR_MESSAGE=$(cat /tmp/bcs_register_response.json 2>/dev/null || echo "Unable to connect to server")
            log_error "Registration failed (HTTP ${HTTP_CODE}): ${ERROR_MESSAGE}"

            if [ "$HTTP_CODE" = "401" ]; then
                log_error "Token is invalid or expired, please re-obtain it from the BCS Web Portal"
                exit 1
            elif [ "$HTTP_CODE" = "400" ]; then
                log_error "Invalid request parameters, check that bot-name is valid (2-64 characters)"
                exit 1
            elif [ $i -lt $RETRY_COUNT ]; then
                log_info "Retrying in ${RETRY_DELAY} seconds..."
                sleep $RETRY_DELAY
            else
                log_error "Registration failed, max retries reached"
                exit 1
            fi
        fi
    done

    # Parse the response
    BOT_UUID=$(echo "$REGISTER_RESPONSE" | jq -r '.bot_uuid')
    BOT_TOKEN=$(echo "$REGISTER_RESPONSE" | jq -r '.bot_token')
fi

if [ -z "$BOT_UUID" ] || [ "$BOT_UUID" = "null" ] || [ -z "$BOT_TOKEN" ] || [ "$BOT_TOKEN" = "null" ]; then
    log_error "Registration response format error, unable to parse bot_uuid or bot_token"
    log_error "Response content: ${REGISTER_RESPONSE}"
    exit 1
fi

log_success "Bot UUID: ${BOT_UUID}"
log_success "Bot Token: ${BOT_TOKEN:0:20}..."
echo ""

# ============================================================================
# Save credentials
# ============================================================================

if [ "$SKIP_REGISTER" = false ]; then
    log_info "Saving Bot credentials..."

    mkdir -p "$BCS_DIR"

    cat > "$SESSION_FILE" <<EOF
{
  "bot_uuid": "${BOT_UUID}",
  "token": "${BOT_TOKEN}",
  "bot_name": "${BOT_NAME}",
  "bcs_url": "${BCS_URL}"
}
EOF

    chmod 600 "$SESSION_FILE"

    log_success "Credentials saved to: ${SESSION_FILE}"
    echo ""
fi

# ============================================================================
# Install BCN plugin
# ============================================================================

log_info "Installing BCN plugin: ${BCN_PLUGIN}..."

# Check if the plugin is already installed
if bcn_plugin_is_listed; then
    log_warn "BCN plugin already installed, uninstalling the old version first"
    openclaw plugins uninstall "$BCN_PLUGIN_PACKAGE" || true
fi

# Install the plugin
log_info "Starting installation, please wait (may take 1-2 minutes)..."

# Start a spinner to show installation progress
spin_chars='|/-\'
spin_pid=""
(
    i=0
    while true; do
        printf "\r  Installing %s " "${spin_chars:$((i%4)):1}"
        i=$((i+1))
        sleep 0.3
    done
) &
spin_pid=$!

# Run the installation, saving output to a temp file for troubleshooting
INSTALL_LOG="/tmp/bcs_plugin_install.log"
openclaw plugins install "$BCN_PLUGIN" > "$INSTALL_LOG" 2>&1 || true

# Stop the spinner
kill "$spin_pid" 2>/dev/null
wait "$spin_pid" 2>/dev/null || true
printf "\r                    \r"

# Verify the installation result
if bcn_plugin_is_listed; then
    log_success "BCN plugin installed successfully"
else
    log_error "BCN plugin installation failed, installation log:"
    cat "$INSTALL_LOG"
    echo ""
    log_info "Check your network connection and npm configuration, or run manually:"
    log_info "  openclaw plugins install ${BCN_PLUGIN}"
    exit 1
fi

echo ""

# ============================================================================
# Configure OpenClaw BCS channel
# ============================================================================

log_info "Writing OpenClaw BCS channel config..."
write_openclaw_bcs_config
echo ""

# ============================================================================
# Restart Gateway
# ============================================================================

log_info "Restarting OpenClaw Gateway..."

# Restart the Gateway
if openclaw gateway restart; then
    log_success "Gateway restarted successfully"
else
    log_error "Gateway restart failed"
    log_info "Please run manually: openclaw gateway restart"
    exit 1
fi

# Wait for the Gateway to start
log_info "Waiting for Gateway to be fully ready (up to ${GATEWAY_RESTART_TIMEOUT} seconds)..."
for i in $(seq 1 $GATEWAY_RESTART_TIMEOUT); do
    if openclaw gateway status 2>/dev/null | grep -q "running"; then
        log_success "Gateway is ready"
        break
    fi

    if [ $i -eq $GATEWAY_RESTART_TIMEOUT ]; then
        log_warn "Gateway startup timed out, please check logs: openclaw gateway logs"
        break
    fi

    sleep 1
done

echo ""

# ============================================================================
# Verify connection
# ============================================================================

log_info "Verifying BCS connection..."

# Check plugin status: the installed plugin is listed as "BCS" with status "enabled".
if openclaw plugins list 2>/dev/null | grep -F "BCS" | grep -q "enabled"; then
    log_success "BCS plugin is enabled"
else
    log_warn "BCS plugin may not be activated properly, please check the Gateway logs"
fi

echo ""

# ============================================================================
# Done
# ============================================================================

log_success "=========================================="
log_success "BCS Bot installed successfully!"
log_success "=========================================="
echo ""
log_info "Bot information:"
log_info "  Name: ${BOT_NAME}"
log_info "  UUID: ${BOT_UUID}"
log_info "  Credential file: ${SESSION_FILE}"
log_info "  BCS server: ${BCS_ENDPOINT}"
echo ""
log_info "Verify connection:"
log_info "  View plugin status: openclaw plugins list"
log_info "  Send a test message: bcs-cli chat --to ${BOT_UUID} --message 'Hello!'"
echo ""
