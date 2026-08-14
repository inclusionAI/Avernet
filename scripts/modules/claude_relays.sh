#!/usr/bin/env bash
# scripts/modules/claude_relays.sh — one local vendored Claude Code relay
[[ -n "${_CLAUDE_RELAYS_SH_LOADED:-}" ]] && return 0
_CLAUDE_RELAYS_SH_LOADED=1

CLAUDE_RELAY_GATEWAY_DIR="${ENGINE_DIR}/src/engine/community/claude_code_gateway"
CLAUDE_RELAY_LOG="${LOG_DIR}/claude_relays.log"
CLAUDE_RELAY_STATE_DIR="${DEP_DIR}/claude-relays"

claude_relays_enabled() {
    claude_profile_enabled
}

claude_relay_pid_file() {
    printf '%s/%s.pid\n' "$CLAUDE_RELAY_STATE_DIR" "$1"
}

claude_relay_data_dir() {
    printf '%s/%s/data\n' "$CLAUDE_RELAY_STATE_DIR" "$1"
}

claude_relay_log_dir() {
    printf '%s/%s/logs\n' "$CLAUDE_RELAY_STATE_DIR" "$1"
}

claude_relay_healthy() {
    curl --noproxy '*' --connect-timeout 1 --max-time 2 -fsS "http://127.0.0.1:${1}/health" 2>/dev/null | jq -e '.ok == true' >/dev/null 2>&1
}

claude_relay_wait_ready() {
    local port="$1" attempt=0
    while [ "$attempt" -lt 40 ]; do
        claude_relay_healthy "$port" && return 0
        sleep 0.5
        attempt=$((attempt + 1))
    done
    return 1
}

claude_relay_find_cli() {
    local candidate
    for candidate in "${CLAUDE_CODE_PATH:-}" "$(command -v claude 2>/dev/null || true)"; do
        [ -n "$candidate" ] && [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
    done
    return 1
}

claude_relay_cli() {
    claude_relay_find_cli && return 0
    log_error "No usable Claude CLI found; set CLAUDE_CODE_PATH or install claude"
    return 1
}

claude_relay_install_cli() {
    if ! command -v npm >/dev/null 2>&1; then
        log_error "npm is required to install Claude Code. Run: ./scripts/singlebox.sh install-tools"
        return 1
    fi

    log_info "Installing Claude Code with npm..."
    if ! npm install -g @anthropic-ai/claude-code --registry="${NPM_REGISTRY_URL}"; then
        log_error "Claude Code installation failed. Install it manually with:"
        log_error "  npm install -g @anthropic-ai/claude-code"
        return 1
    fi
    hash -r 2>/dev/null || true
    if ! claude_relay_find_cli >/dev/null; then
        log_error "Claude Code was installed but the claude executable is not in PATH."
        return 1
    fi
    log_info "Claude Code installed successfully."
}

claude_relays_setup() {
    claude_relays_enabled || return 0
    claude_profile_validate_config || return 1
    [ -f "${CLAUDE_RELAY_GATEWAY_DIR}/package.json" ] || { log_error "Vendored Claude gateway missing"; return 1; }
    if [ ! -f "${CLAUDE_RELAY_GATEWAY_DIR}/dist/esm/server.js" ] || find "${CLAUDE_RELAY_GATEWAY_DIR}/src" -type f -newer "${CLAUDE_RELAY_GATEWAY_DIR}/dist/esm/server.js" -print -quit | grep -q .; then
        log_info "Building vendored Claude Code gateway..."
        (cd "$CLAUDE_RELAY_GATEWAY_DIR" && npm install --include=dev --ignore-scripts --no-audit --no-fund --registry="${NPM_REGISTRY_URL}" && npm run prepublishOnly) >> "$CLAUDE_RELAY_LOG" 2>&1 || {
            log_error "Claude gateway build failed; check ${CLAUDE_RELAY_LOG}"
            return 1
        }
    fi
}

claude_relays_prereqs() {
    claude_relays_enabled || return 0
    claude_profile_validate_config || return 1
    check_command node || { prereq_error "node not found"; return 1; }
    check_command jq || { prereq_error "jq not found"; return 1; }
    claude_relay_cli >/dev/null || return 1
}

claude_relay_prompt_anthropic_base_url() {
    local openai_base_url="${OPENCLAW_OPENAI_BASE_URL:-}"
    local answer=""
    [ -n "$openai_base_url" ] || {
        log_error "ANTHROPIC_BASE_URL is not set and no OPENCLAW_OPENAI_BASE_URL is available as an editing hint."
        return 1
    }

    log_warn "ANTHROPIC_BASE_URL is not set."
    log_warn "Current OpenAI-compatible URL: ${openai_base_url}"
    log_warn "Claude Code requires this URL to support the Anthropic Messages API."
    if [ ! -t 0 ] || [ ! -r /dev/tty ] || [ ! -w /dev/tty ]; then
        log_error "Set ANTHROPIC_BASE_URL explicitly for non-interactive startup."
        return 1
    fi

    printf 'Anthropic-compatible base URL [%s]: ' "$openai_base_url" >&2
    read -r answer </dev/tty || return 1
    ANTHROPIC_BASE_URL="${answer:-$openai_base_url}"
    export ANTHROPIC_BASE_URL
}

claude_relay_prepare_env_local_model() {
    if [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
        ANTHROPIC_AUTH_TOKEN="${OPENCLAW_OPENAI_API_KEY:-}"
        export ANTHROPIC_AUTH_TOKEN
        [ -n "$ANTHROPIC_AUTH_TOKEN" ] && log_info "Claude Code auth token defaults to OPENCLAW_OPENAI_API_KEY."
    fi
    if [ -z "${ANTHROPIC_MODEL:-}" ]; then
        ANTHROPIC_MODEL="${OPENCLAW_OPENAI_MODEL_ID:-}"
        export ANTHROPIC_MODEL
        [ -n "$ANTHROPIC_MODEL" ] && log_info "Claude Code model defaults to OPENCLAW_OPENAI_MODEL_ID (${ANTHROPIC_MODEL})."
    fi
    if [ -z "${ANTHROPIC_BASE_URL:-}" ]; then
        if [ "${HYBRID_RESTART_FROM_STATE:-0}" = "1" ]; then
            log_error "The previous hybrid runtime has no reusable ANTHROPIC_BASE_URL; restart will not prompt for a replacement."
            log_error "Run start hybrid once to choose and save an Anthropic-compatible URL."
            return 1
        fi
        claude_relay_prompt_anthropic_base_url || return 1
    fi
    claude_relay_validate_env_local_model
}

claude_relay_validate_env_local_model() {
    local missing=()
    [ -n "${ANTHROPIC_MODEL:-}" ] || missing+=(ANTHROPIC_MODEL)
    if [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
        missing+=("ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY")
    fi
    [ -n "${ANTHROPIC_BASE_URL:-}" ] || missing+=(ANTHROPIC_BASE_URL)
    if [ "${#missing[@]}" -gt 0 ]; then
        log_error "Claude Code .env.local configuration is incomplete: ${missing[*]}"
        log_error "Claude Code requires an Anthropic-compatible Messages API endpoint."
        log_error "If the upstream only supports OpenAI APIs, configure an Anthropic-compatible gateway and set ANTHROPIC_BASE_URL to it."
        return 1
    fi
}

claude_relays_manual_model_env() {
    CLAUDE_RELAY_MANUAL_MODEL_ENV=()
    [ "${HYBRID_CLAUDE_CONFIG_MODE:-}" != "user" ] || return 0
    [ "${SINGLEBOX_MODEL_CONFIG_MODE:-}" = "manual" ] || return 0

    claude_relay_validate_env_local_model || return 1
    local model="${ANTHROPIC_MODEL}"
    [ -n "${ANTHROPIC_BASE_URL:-}" ] && CLAUDE_RELAY_MANUAL_MODEL_ENV+=("ANTHROPIC_BASE_URL=${ANTHROPIC_BASE_URL}")
    if [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ] && [ -n "${ANTHROPIC_BASE_URL:-}" ]; then
        CLAUDE_RELAY_MANUAL_MODEL_ENV+=("ANTHROPIC_AUTH_TOKEN=${ANTHROPIC_AUTH_TOKEN}")
    else
        CLAUDE_RELAY_MANUAL_MODEL_ENV+=("ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}")
    fi
    CLAUDE_RELAY_MANUAL_MODEL_ENV+=(
        "ANTHROPIC_MODEL=${model}"
        "ANTHROPIC_SMALL_FAST_MODEL=${model}"
    )
    log_info "Claude relay model provider resolved from .env.local Anthropic configuration (model=${model})"
}

claude_relays_start() {
    claude_relays_enabled || return 0
    claude_relays_setup || return 1
    local role name summary port config_dir workspace model prompt_file permission pid_file cli model_source
    IFS=$'\x1f' read -r role name summary port config_dir workspace model prompt_file permission < <(claude_profile_entries)
    if [ "${HYBRID_CLAUDE_CONFIG_MODE:-}" != "user" ] && [ -z "$model" ]; then
        log_error "Claude relay model was not resolved from the selected Singlebox configuration"
        return 1
    fi
    cli="$(claude_relay_cli)" || return 1
    mkdir -p "$config_dir" "$workspace" "$(claude_relay_data_dir "$role")" "$(claude_relay_log_dir "$role")" "$CLAUDE_RELAY_STATE_DIR" "$LOG_DIR"
    pid_file="$(claude_relay_pid_file "$role")"
    if [ -f "$pid_file" ]; then
        stop_process_if_owned "$(cat "$pid_file" 2>/dev/null || true)" "$PROJECT_ROOT" "Claude ${role} relay" || true
        rm -f "$pid_file"
    fi
    stop_port_processes_if_owned "$port" "$PROJECT_ROOT" "Claude ${role} relay" || true
    require_port_available_after_owned_stop "$port" "Claude ${role} relay" || return 1
    model_source=""
    claude_relays_manual_model_env "$model" || return 1
    if [ "${HYBRID_CLAUDE_CONFIG_MODE:-}" = "user" ] && [ -f "$HOME/.claude/settings.json" ]; then
        model_source="$HOME/.claude/settings.json"
    elif [ "${SINGLEBOX_MODEL_CONFIG_MODE:-}" != "manual" ] && [ ! -f "${config_dir}/settings.json" ] && [ -f "$HOME/.claude/settings.json" ]; then
        model_source="$HOME/.claude/settings.json"
    fi
    local relay_default_model_env=()
    [ -n "$model" ] && relay_default_model_env=("RELAY_DEFAULT_MODEL=${model}")
    log_info "Starting Claude relay role=${role} port=${port}"
    (
        cd "$CLAUDE_RELAY_GATEWAY_DIR"
        exec env PORT="$port" RELAY_CLAUDE_CONFIG_DIR="$config_dir" RELAY_MODEL_SETTINGS_SOURCE="$model_source" \
            "${relay_default_model_env[@]}" \
            RELAY_DEFAULT_PERMISSION_MODE="$permission" RELAY_DEFAULT_CWD="$workspace" \
            RELAY_DATA_DIR="$(claude_relay_data_dir "$role")" RELAY_LOG_DIR="$(claude_relay_log_dir "$role")" \
            RELAY_SYSTEM_PROMPT_FILE="$prompt_file" RELAY_SYSTEM_PROMPT_ROOT="$(dirname "$prompt_file")" \
            CLAUDE_CODE_PATH="$cli" \
            "${CLAUDE_RELAY_MANUAL_MODEL_ENV[@]}" \
            perl -MPOSIX=setsid -e 'setsid() or die "setsid failed: $!\\n"; exec @ARGV' node dist/esm/server.js
    ) </dev/null >> "$CLAUDE_RELAY_LOG" 2>&1 &
    printf '%s\n' "$!" > "$pid_file"
    claude_relay_wait_ready "$port" || { log_error "Claude relay failed readiness; check ${CLAUDE_RELAY_LOG}"; return 1; }
}

claude_relays_stop() {
    claude_relays_enabled || return 0
    local role name summary port config_dir workspace model prompt_file permission pid_file
    IFS=$'\x1f' read -r role name summary port config_dir workspace model prompt_file permission < <(claude_profile_entries)
    pid_file="$(claude_relay_pid_file "$role")"
    [ -f "$pid_file" ] && stop_process_if_owned "$(cat "$pid_file" 2>/dev/null || true)" "$PROJECT_ROOT" "Claude ${role} relay" || true
    rm -f "$pid_file"
}

claude_relays_clean() {
    local pid_file pid
    if [ -d "$CLAUDE_RELAY_STATE_DIR" ]; then
        for pid_file in "$CLAUDE_RELAY_STATE_DIR"/*.pid; do
            [ -f "$pid_file" ] || continue
            pid="$(cat "$pid_file" 2>/dev/null || true)"
            stop_process_if_owned "$pid" "$PROJECT_ROOT" "Claude relay" || return 1
            rm -f "$pid_file"
        done
        rm -rf "$CLAUDE_RELAY_STATE_DIR"
    fi
    rm -f "$CLAUDE_RELAY_LOG"
}

claude_relays_ready() {
    claude_relays_enabled || return 0
    local role name summary port rest
    IFS=$'\x1f' read -r role name summary port rest < <(claude_profile_entries)
    claude_relay_wait_ready "$port"
}

claude_relays_status() {
    claude_relays_enabled || return 0
    local role name summary port rest
    IFS=$'\x1f' read -r role name summary port rest < <(claude_profile_entries)
    if claude_relay_healthy "$port"; then echo "  Claude relay (${role}): Running (port ${port})"; else echo "  Claude relay (${role}): Stopped"; fi
}
