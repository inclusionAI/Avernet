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

claude_relay_cli() {
    local candidate
    for candidate in "${CLAUDE_CODE_PATH:-}" "$(command -v claude 2>/dev/null || true)"; do
        [ -n "$candidate" ] && [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
    done
    log_error "No usable Claude CLI found; set CLAUDE_CODE_PATH or install claude"
    return 1
}

claude_relays_setup() {
    claude_relays_enabled || return 0
    claude_profile_validate_config || return 1
    [ -f "${CLAUDE_RELAY_GATEWAY_DIR}/package.json" ] || { log_error "Vendored Claude gateway missing"; return 1; }
    if [ ! -f "${CLAUDE_RELAY_GATEWAY_DIR}/dist/esm/server.js" ] || find "${CLAUDE_RELAY_GATEWAY_DIR}/src" -type f -newer "${CLAUDE_RELAY_GATEWAY_DIR}/dist/esm/server.js" -print -quit | grep -q .; then
        log_info "Building vendored Claude Code gateway..."
        (cd "$CLAUDE_RELAY_GATEWAY_DIR" && npm install --include=dev --ignore-scripts --no-audit --no-fund && npm run prepublishOnly) >> "$CLAUDE_RELAY_LOG" 2>&1 || {
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

claude_relays_start() {
    claude_relays_enabled || return 0
    claude_relays_setup || return 1
    local role name summary port config_dir workspace model prompt_file permission pid_file cli model_source
    IFS=$'\x1f' read -r role name summary port config_dir workspace model prompt_file permission < <(claude_profile_entries)
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
    if [ ! -f "${config_dir}/settings.json" ] && [ -f "$HOME/.claude/settings.json" ]; then
        model_source="$HOME/.claude/settings.json"
    fi
    log_info "Starting Claude relay role=${role} port=${port}"
    (
        cd "$CLAUDE_RELAY_GATEWAY_DIR"
        exec env PORT="$port" RELAY_CLAUDE_CONFIG_DIR="$config_dir" RELAY_MODEL_SETTINGS_SOURCE="$model_source" \
            RELAY_DEFAULT_MODEL="$model" RELAY_DEFAULT_PERMISSION_MODE="$permission" RELAY_DEFAULT_CWD="$workspace" \
            RELAY_DATA_DIR="$(claude_relay_data_dir "$role")" RELAY_LOG_DIR="$(claude_relay_log_dir "$role")" \
            RELAY_SYSTEM_PROMPT_FILE="$prompt_file" RELAY_SYSTEM_PROMPT_ROOT="$(dirname "$prompt_file")" \
            CLAUDE_CODE_PATH="$cli" \
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
