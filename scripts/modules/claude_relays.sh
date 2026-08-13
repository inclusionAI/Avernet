#!/usr/bin/env bash
# scripts/modules/claude_relays.sh — isolated Claude Code gateway relays
[[ -n "${_CLAUDE_RELAYS_SH_LOADED:-}" ]] && return 0
_CLAUDE_RELAYS_SH_LOADED=1

CLAUDE_RELAY_LOG="${LOG_DIR}/claude_relays.log"
CLAUDE_RELAY_GATEWAY_DIR="${ENGINE_DIR}/src/engine/community/claude_code_gateway"
CLAUDE_RELAY_STATE_DIR="${DEP_DIR}/claude_relays"
CLAUDE_BOTS_STATE_FILE="${DEP_DIR}/claude_bots.state.json"

claude_bots_select_runtime_files() {
    # Keep the 3-role legacy mixed topology's state untouched. The merchant
    # profile has one deliberately overlapping human-facing name, so it needs
    # its own Bot and Provider state/credential files rather than reusing a
    # stale planner/developer/reviewer registration.
    if ! type -t claude_profile_enabled &>/dev/null || ! claude_profile_enabled; then
        return 0
    fi
    CLAUDE_BOTS_STATE_FILE="${DEP_DIR}/claude_bots.merchant_hybrid.state.json"
    BCS_BAAS_PROVIDER_LOG="${LOG_DIR}/bcs_baas_provider.merchant_hybrid.log"
    BCS_BAAS_PROVIDER_PID_FILE="${DEP_DIR}/bcs_baas_provider.merchant_hybrid.pid"
    BCS_BAAS_PROVIDER_STATE_FILE="${DEP_DIR}/bcs_baas_provider.merchant_hybrid.state.json"
    BCS_BAAS_PROVIDER_TOKEN_FILE="${DEP_DIR}/bcs_baas_provider.merchant_hybrid.tokens.json"
}

claude_bots_enabled() {
    [ -n "${CLAUDE_BOTS_CONFIG:-}" ] || (type -t claude_profile_enabled &>/dev/null && claude_profile_enabled)
}

claude_expand_path() {
    local value="$1"
    case "$value" in
        "~") printf '%s\n' "$HOME" ;;
        "~/"*) printf '%s/%s\n' "$HOME" "${value#~/}" ;;
        *) printf '%s\n' "$value" ;;
    esac
}

claude_bots_config_path() {
    claude_expand_path "${CLAUDE_BOTS_CONFIG:-}"
}

claude_relay_can_initialize_profile_config() {
    type -t claude_profile_enabled &>/dev/null && claude_profile_enabled \
        && [ "${SINGLEBOX_MODEL_CONFIG_MODE:-}" = "home" ] \
        && [ -f "$HOME/.claude/settings.json" ]
}

claude_relay_ensure_config_dir() {
    local role="$1" config_dir="$2"
    [ -d "$config_dir" ] && return 0
    if ! claude_relay_can_initialize_profile_config; then
        log_error "Claude ${role} config directory does not exist: ${config_dir}"
        return 1
    fi
    if ! mkdir -p "$config_dir"; then
        log_error "Failed to initialize isolated Claude ${role} config directory: ${config_dir}"
        return 1
    fi
    log_info "Initialized isolated Claude ${role} config directory from home model settings mode"
}

claude_bots_validate_config() {
    claude_bots_enabled || return 0
    if [ -n "${CLAUDE_BOTS_CONFIG:-}" ] && type -t claude_profile_enabled &>/dev/null && claude_profile_enabled; then
        log_error "--claude-bots-config and --claude-profile-dir cannot be used together"
        return 1
    fi
    if type -t claude_profile_enabled &>/dev/null && claude_profile_enabled; then
        claude_bots_select_runtime_files
        claude_profile_validate_config
        return
    fi
    local config_path
    config_path="$(claude_bots_config_path)"
    if [ ! -f "$config_path" ]; then
        log_error "Claude bot config does not exist: ${config_path}"
        return 1
    fi
    if ! python3 - "$config_path" <<'PY'
import json
import os
import re
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as stream:
        config = json.load(stream)
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid JSON: {exc}")

bots = config.get("bots") if isinstance(config, dict) else None
required = {
    "planner": 18910,
    "developer": 18911,
    "reviewer": 18912,
}
if not isinstance(bots, list) or len(bots) != len(required):
    raise SystemExit("bots must contain exactly planner, developer, and reviewer")

for field, default in (("entity_id", "mock-user"), ("entity_type", "staff")):
    value = config.get(field, default)
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]+", value):
        raise SystemExit(f"{field} must contain only letters, numbers, ., _, @, or -")

seen = set()
seen_config_dirs = set()
seen_workspaces = set()
seen_names = set()
for item in bots:
    if not isinstance(item, dict):
        raise SystemExit("every bots item must be an object")
    role = item.get("role")
    if role not in required or role in seen:
        raise SystemExit("roles must be unique planner, developer, reviewer")
    if item.get("relay_port") != required[role]:
        raise SystemExit(f"{role} relay_port must be {required[role]}")
    for field in ("claude_config_dir", "workspace"):
        value = item.get(field)
        if not isinstance(value, str) or not value.strip():
            raise SystemExit(f"{role}.{field} must be a non-empty string")
        if not os.path.isabs(os.path.expanduser(value)):
            raise SystemExit(f"{role}.{field} must resolve to an absolute path")
    for field in ("name", "description", "model"):
        value = item.get(field)
        if value is not None and not isinstance(value, str):
            raise SystemExit(f"{role}.{field} must be a string when supplied")
    for field in ("claude_config_dir", "workspace", "name", "description", "model"):
        value = item.get(field)
        if isinstance(value, str) and ("\t" in value or "\n" in value or "\r" in value):
            raise SystemExit(f"{role}.{field} cannot contain a tab or newline")
    config_dir = os.path.realpath(os.path.expanduser(item["claude_config_dir"]))
    workspace = os.path.realpath(os.path.expanduser(item["workspace"]))
    name = (item.get("name") or f"Claude {role.title()}").strip()
    if config_dir in seen_config_dirs:
        raise SystemExit("claude_config_dir values must be unique per role")
    if workspace in seen_workspaces:
        raise SystemExit("workspace values must be unique per role")
    if not name or name in seen_names:
        raise SystemExit("bot names must be unique and non-empty")
    seen_config_dirs.add(config_dir)
    seen_workspaces.add(workspace)
    seen_names.add(name)
    seen.add(role)
if seen != set(required):
    raise SystemExit("roles must be exactly planner, developer, reviewer")
PY
    then
        log_error "Invalid --claude-bots-config (expected strict planner/developer/reviewer 18910-18912 schema)"
        return 1
    fi
}

# Emits unit-separator-delimited role/name/description/port/config-dir/workspace/model/prompt-file/permission-mode.
# All calls validate first, so the shell does not need to parse untrusted JSON.
claude_bots_entries() {
    if type -t claude_profile_enabled &>/dev/null && claude_profile_enabled; then
        claude_profile_entries
        return
    fi
    local config_path
    config_path="$(claude_bots_config_path)"
    python3 - "$config_path" <<'PY'
import json
import os
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    bots = json.load(stream)["bots"]
for bot in sorted(bots, key=lambda item: item["relay_port"]):
    values = [
        bot["role"],
        bot.get("name") or f"Claude {bot['role'].title()}",
        bot.get("description") or f"Claude Code {bot['role']} bot",
        str(bot["relay_port"]),
        os.path.expanduser(bot["claude_config_dir"]),
        os.path.expanduser(bot["workspace"]),
        bot.get("model") or "",
        "",
        "",
    ]
    if any("\x1f" in value or "\t" in value or "\n" in value or "\r" in value for value in values):
        raise SystemExit("unit-separator/tab/newline is not allowed in Claude bot config values")
    print("\x1f".join(values))
PY
}

claude_relay_role_prompt() {
    case "$1" in
        planner) printf '%s\n' 'You are the planner in a local multi-agent team. Clarify goals, decompose work, and return concise executable plans. Do not modify files or execute commands unless explicitly asked.' ;;
        developer) printf '%s\n' 'You are the developer in a local multi-agent team. Implement only the requested change, explain concrete tradeoffs, and avoid side effects outside your assigned workspace.' ;;
        reviewer) printf '%s\n' 'You are the reviewer in a local multi-agent team. Inspect for correctness, security, regressions, and missing tests. Do not modify files or execute commands unless explicitly asked.' ;;
        *) return 1 ;;
    esac
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

# Run only the CLI's local version command before giving it to the SDK.  A
# healthy relay HTTP endpoint alone cannot prove that the configured Claude
# executable is usable: a broken native installation otherwise fails only
# after BCS has accepted a user message.
claude_relay_cli_usable() {
    local cli_path="$1"

    [ -x "$cli_path" ] || return 1
    # Use Node's synchronous timeout instead of a background shell job.  The
    # latter prints an asynchronous "Killed" notification when macOS
    # terminates a broken native Claude installation, which makes successful
    # automatic fallback look like a startup failure.
    node - "$cli_path" <<'NODE' >/dev/null 2>&1
const { spawnSync } = require('node:child_process');

const result = spawnSync(process.argv[2], ['--version'], {
  stdio: 'ignore',
  timeout: 5_000,
});
process.exit(result.status === 0 ? 0 : 1);
NODE
}

# Resolve one known-good executable for all isolated relays.  An explicit
# override is deliberately fail-closed; automatic discovery can skip a stale
# self-updating native binary in favor of an existing npm installation.
claude_relay_resolve_cli() {
    local configured="${CLAUDE_CODE_PATH:-}" candidate path_cli
    local candidates=()

    if [ -n "$configured" ]; then
        if claude_relay_cli_usable "$configured"; then
            printf '%s\n' "$configured"
            return 0
        fi
        log_error "Configured CLAUDE_CODE_PATH failed the Claude CLI version preflight"
        return 1
    fi

    path_cli="$(command -v claude 2>/dev/null || true)"
    [ -n "$path_cli" ] && candidates+=("$path_cli")
    candidates+=("$HOME/.local/bin/claude" "/opt/homebrew/bin/claude" "/usr/local/bin/claude")
    for candidate in "${candidates[@]}"; do
        if claude_relay_cli_usable "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    log_error "No usable Claude CLI passed the local version preflight; set CLAUDE_CODE_PATH to a working executable"
    return 1
}

claude_relays_setup() {
    claude_bots_enabled || return 0
    claude_bots_validate_config || return 1
    mkdir -p "$LOG_DIR"
    if ! check_command node; then
        log_error "node is required for the vendored Claude Code gateway"
        return 1
    fi
    if [ ! -f "${CLAUDE_RELAY_GATEWAY_DIR}/package.json" ]; then
        log_error "Vendored Claude Code gateway is missing: ${CLAUDE_RELAY_GATEWAY_DIR}"
        return 1
    fi
    if [ ! -f "${CLAUDE_RELAY_GATEWAY_DIR}/dist/esm/server.js" ] \
        || find "${CLAUDE_RELAY_GATEWAY_DIR}/src" -type f -newer "${CLAUDE_RELAY_GATEWAY_DIR}/dist/esm/server.js" -print -quit | grep -q .; then
        log_info "Building vendored Claude Code gateway..."
        if ! (
            cd "$CLAUDE_RELAY_GATEWAY_DIR" &&
            npm install --include=dev --ignore-scripts --no-audit --no-fund &&
            npm run prepublishOnly
        ) >> "$CLAUDE_RELAY_LOG" 2>&1; then
            log_error "Claude Code gateway build failed; check ${CLAUDE_RELAY_LOG}"
            return 1
        fi
    fi
}

claude_relays_prereqs() {
    claude_bots_enabled || return 0
    claude_bots_validate_config || return 1
    local has_error=false
    if check_command node; then
        prereq_ok "node: $(command -v node)"
    else
        prereq_error "node not found; required for Claude Code relays"
        has_error=true
    fi
    if check_command jq; then
        prereq_ok "jq: $(command -v jq)"
    else
        prereq_error "jq not found; required for Claude Code relay health checks"
        has_error=true
    fi
    if check_command perl; then
        prereq_ok "perl: $(command -v perl)"
    else
        prereq_error "perl not found; required to isolate Claude relay process sessions"
        has_error=true
    fi
    if claude_relay_resolve_cli >/dev/null; then
        prereq_ok "Claude CLI version preflight passed"
    else
        prereq_error "No usable Claude CLI found; see the relay diagnostic above"
        has_error=true
    fi
    local role name description port config_dir workspace model prompt_file permission_mode
    while IFS=$'\x1f' read -r role name description port config_dir workspace model prompt_file permission_mode; do
        if [ -d "$config_dir" ]; then
            prereq_ok "Claude ${role} config: ${config_dir}"
        elif claude_relay_can_initialize_profile_config; then
            prereq_ok "Claude ${role} config will be initialized from home model settings"
        else
            prereq_error "Claude ${role} config directory not found: ${config_dir}"
            has_error=true
        fi
    done < <(claude_bots_entries)
    if [ "$has_error" = true ]; then
        return 1
    fi
}

claude_relay_wait_ready() {
    local port="$1" attempts=0
    while [ "$attempts" -lt 40 ]; do
        if claude_relay_healthy "$port"; then
            return 0
        fi
        sleep 0.5
        attempts=$((attempts + 1))
    done
    return 1
}

claude_relay_healthy() {
    local port="$1"
    curl --noproxy '*' --connect-timeout 1 --max-time 2 -fsS "http://127.0.0.1:${port}/health" 2>/dev/null \
        | jq -e '.ok == true' >/dev/null 2>&1
}

claude_relay_start_detached() {
    local port="$1" config_dir="$2" model_settings_source="$3" model="$4" permission_mode="$5" workspace="$6" data_dir="$7" log_dir="$8" prompt="$9" prompt_file="${10}" claude_cli="${11}" prompt_root=""
    if [ -n "$prompt_file" ]; then
        prompt_root="$(dirname "$prompt_file")"
    fi

    # A relay must not share the invoking singlebox shell's process group.
    # Interactive shells and task runners may terminate that group after the
    # command returns; without a new session a healthy streaming chat is cut
    # off with an EOF even though BCS/BaaS are still running.  The PID remains
    # the node process, so normal singlebox stop ownership checks still apply.
    (
        cd "$CLAUDE_RELAY_GATEWAY_DIR"
        exec env \
            PORT="$port" \
            RELAY_CLAUDE_CONFIG_DIR="$config_dir" \
            RELAY_MODEL_SETTINGS_SOURCE="$model_settings_source" \
            RELAY_DEFAULT_MODEL="$model" \
            RELAY_DEFAULT_PERMISSION_MODE="$permission_mode" \
            RELAY_DEFAULT_CWD="$workspace" \
            RELAY_DATA_DIR="$data_dir" \
            RELAY_LOG_DIR="$log_dir" \
            RELAY_SYSTEM_PROMPT_PREFIX="$prompt" \
            RELAY_SYSTEM_PROMPT_FILE="$prompt_file" \
            RELAY_SYSTEM_PROMPT_ROOT="$prompt_root" \
            CLAUDE_CODE_PATH="$claude_cli" \
            nohup perl -MPOSIX=setsid -e 'setsid() or die "setsid failed: $!\\n"; exec @ARGV' \
            node dist/esm/server.js
    ) </dev/null >> "$CLAUDE_RELAY_LOG" 2>&1 &
}

claude_relays_start() {
    claude_bots_enabled || return 0
    claude_bots_validate_config || return 1
    claude_relays_setup || return 1
    mkdir -p "$CLAUDE_RELAY_STATE_DIR" "$LOG_DIR"

    local role name description port config_dir workspace model prompt_file permission_mode pid_file prompt model_settings_source claude_cli
    claude_cli="$(claude_relay_resolve_cli)" || return 1
    log_info "Claude relay CLI preflight passed; starting isolated relays"
    while IFS=$'\x1f' read -r role name description port config_dir workspace model prompt_file permission_mode; do
        claude_relay_ensure_config_dir "$role" "$config_dir" || return 1
        mkdir -p "$workspace" "$(claude_relay_data_dir "$role")" "$(claude_relay_log_dir "$role")"
        pid_file="$(claude_relay_pid_file "$role")"
        if [ -f "$pid_file" ]; then
            stop_process_if_owned "$(cat "$pid_file" 2>/dev/null || true)" "$PROJECT_ROOT" "Claude ${role} relay" || true
            rm -f "$pid_file"
        fi
        stop_port_processes_if_owned "$port" "$PROJECT_ROOT" "Claude ${role} relay" || true
        require_port_available_after_owned_stop "$port" "Claude ${role} relay" || return 1
        if [ -n "$prompt_file" ]; then
            if [ ! -f "$prompt_file" ]; then
                log_error "Claude ${role} system prompt file does not exist"
                return 1
            fi
            prompt=""
        else
            prompt="$(claude_relay_role_prompt "$role")" || return 1
        fi
        model_settings_source=""
        if [ ! -f "${config_dir}/settings.json" ] && [ -f "$HOME/.claude/settings.json" ]; then
            model_settings_source="$HOME/.claude/settings.json"
            log_info "Claude ${role} relay will use the local model-provider settings source"
        fi
        log_info "Starting vendored Claude Code relay: role=${role} port=${port}"
        claude_relay_start_detached \
            "$port" "$config_dir" "$model_settings_source" "$model" "$permission_mode" "$workspace" \
            "$(claude_relay_data_dir "$role")" "$(claude_relay_log_dir "$role")" \
            "$prompt" "$prompt_file" "$claude_cli"
        echo $! > "$pid_file"
        log_info "Claude ${role} relay launched in an isolated process session"
        if ! claude_relay_wait_ready "$port"; then
            log_error "Claude ${role} relay did not become ready; check ${CLAUDE_RELAY_LOG}"
            return 1
        fi
    done < <(claude_bots_entries)
}

claude_relays_stop() {
    claude_bots_enabled || return 0
    local role name description port config_dir workspace model prompt_file permission_mode pid_file
    while IFS=$'\x1f' read -r role name description port config_dir workspace model prompt_file permission_mode; do
        pid_file="$(claude_relay_pid_file "$role")"
        if [ -f "$pid_file" ]; then
            stop_process_if_owned "$(cat "$pid_file" 2>/dev/null || true)" "$PROJECT_ROOT" "Claude ${role} relay" || true
            rm -f "$pid_file"
        fi
    done < <(claude_bots_entries)
}

claude_relays_ready() {
    claude_bots_enabled || return 0
    local role name description port config_dir workspace model prompt_file permission_mode
    while IFS=$'\x1f' read -r role name description port config_dir workspace model prompt_file permission_mode; do
        claude_relay_wait_ready "$port" || return 1
    done < <(claude_bots_entries)
}

claude_relays_status() {
    claude_bots_enabled || return 0
    local role name description port config_dir workspace model prompt_file permission_mode
    while IFS=$'\x1f' read -r role name description port config_dir workspace model prompt_file permission_mode; do
        if claude_relay_healthy "$port"; then
            echo "  Claude relay (${role}): Running (port ${port})"
        else
            echo "  Claude relay (${role}): Stopped"
        fi
    done < <(claude_bots_entries)
}

claude_relays_help() {
    echo "claude_relays - isolated vendored Claude Code gateway relays (mixed mode only)"
}
