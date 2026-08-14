#!/usr/bin/env bash
# scripts/modules/hybrid.sh — OpenClaw stack with optional Claude Code bots
[[ -n "${_HYBRID_SH_LOADED:-}" ]] && return 0
_HYBRID_SH_LOADED=1

# The base hybrid mode runs OpenClaw profiles directly. Claude-specific
# services are included only when both Claude profile options are supplied.
HYBRID_OPENCLAW_SETUP_ORDER=(bcs bcsfuse bots frontend)
HYBRID_OPENCLAW_START_ORDER=(bcs bcsfuse bots frontend)
HYBRID_OPENCLAW_STOP_ORDER=(frontend bots bcsfuse bcs)
HYBRID_CLAUDE_SETUP_ORDER=(claude_relays baas backend bcs bcsfuse bots claude_bots bcs_baas_provider frontend)
HYBRID_CLAUDE_START_ORDER=(claude_relays baas backend bcs bcsfuse bots claude_bots bcs_baas_provider frontend)
HYBRID_CLAUDE_STOP_ORDER=(frontend bcs_baas_provider claude_bots bots bcsfuse bcs backend baas claude_relays)
HYBRID_SETUP_ORDER=()
HYBRID_START_ORDER=()
HYBRID_STOP_ORDER=()
HYBRID_STATE_FILE="${HYBRID_STATE_FILE:-${DEP_DIR}/hybrid.state.json}"

hybrid_claude_enabled() {
    [ -n "${CLAUDE_PROFILE_DIR:-}" ]
}

hybrid_configure_mode() {
    if hybrid_claude_enabled; then
        HYBRID_SETUP_ORDER=("${HYBRID_CLAUDE_SETUP_ORDER[@]}")
        HYBRID_START_ORDER=("${HYBRID_CLAUDE_START_ORDER[@]}")
        HYBRID_STOP_ORDER=("${HYBRID_CLAUDE_STOP_ORDER[@]}")
        export HYBRID_CLAUDE_ACTIVE=1
        # Compatibility for existing module integrations and local state.
        export MERCHANT_HYBRID_ACTIVE=1
    else
        HYBRID_SETUP_ORDER=("${HYBRID_OPENCLAW_SETUP_ORDER[@]}")
        HYBRID_START_ORDER=("${HYBRID_OPENCLAW_START_ORDER[@]}")
        HYBRID_STOP_ORDER=("${HYBRID_OPENCLAW_STOP_ORDER[@]}")
        unset HYBRID_CLAUDE_ACTIVE MERCHANT_HYBRID_ACTIVE
    fi
}

hybrid_resolve_profile_path() {
    local value="$1"
    case "$value" in
        /*) ;;
        *) value="${PROJECT_ROOT}/${value}" ;;
    esac
    (cd "$value" 2>/dev/null && pwd -P) || printf '%s\n' "$value"
}

hybrid_profile_paths_match() {
    [ "$(hybrid_resolve_profile_path "$1")" = "$(hybrid_resolve_profile_path "$2")" ]
}

hybrid_save_runtime_state() {
    local mode="openclaw" tmp_file
    hybrid_claude_enabled && mode="claude"
    tmp_file="${HYBRID_STATE_FILE}.$$.tmp"
    mkdir -p "$(dirname "$HYBRID_STATE_FILE")"
    umask 077
    if ! jq -n \
        --arg mode "$mode" \
        --arg bots_profile_dir "${BOTS_PROFILE_DIR}" \
        --arg excluded_profile_source "${BOTS_EXCLUDED_PROFILE_SOURCE:-}" \
        --arg claude_profile_dir "${CLAUDE_PROFILE_DIR:-}" \
        '{mode: $mode, bots_profile_dir: $bots_profile_dir, excluded_profile_source: $excluded_profile_source, claude_profile_dir: $claude_profile_dir}' \
        > "$tmp_file"; then
        rm -f "$tmp_file"
        log_error "Failed to save hybrid runtime state"
        return 1
    fi
    if ! chmod 600 "$tmp_file" || ! mv "$tmp_file" "$HYBRID_STATE_FILE"; then
        rm -f "$tmp_file"
        log_error "Failed to install hybrid runtime state"
        return 1
    fi
}

hybrid_clear_runtime_state() {
    rm -f "$HYBRID_STATE_FILE"
}

hybrid_restore_runtime_state() {
    [ -f "$HYBRID_STATE_FILE" ] || return 0
    if ! jq -e '
        type == "object"
        and (.mode == "openclaw" or .mode == "claude")
        and ((.bots_profile_dir | type) == "string")
        and ((.bots_profile_dir | length) > 0)
        and ((.excluded_profile_source | type) == "string")
        and ((.claude_profile_dir | type) == "string")
        and (if .mode == "claude"
             then ((.excluded_profile_source | length) > 0) and ((.claude_profile_dir | length) > 0)
             else .excluded_profile_source == "" and .claude_profile_dir == ""
             end)
    ' "$HYBRID_STATE_FILE" >/dev/null 2>&1; then
        log_error "Invalid hybrid runtime state: ${HYBRID_STATE_FILE}"
        return 1
    fi

    local mode state_bots_profile state_excluded_profile state_claude_profile
    mode="$(jq -r '.mode' "$HYBRID_STATE_FILE")"
    state_bots_profile="$(jq -r '.bots_profile_dir' "$HYBRID_STATE_FILE")"
    state_excluded_profile="$(jq -r '.excluded_profile_source' "$HYBRID_STATE_FILE")"
    state_claude_profile="$(jq -r '.claude_profile_dir' "$HYBRID_STATE_FILE")"

    if [ -n "${BOTS_PROFILE_DIR:-}" ] && ! hybrid_profile_paths_match "$BOTS_PROFILE_DIR" "$state_bots_profile"; then
        log_error "--profile-dir does not match the active hybrid runtime"
        return 1
    fi
    export BOTS_PROFILE_DIR="$state_bots_profile"

    if [ "$mode" = "claude" ]; then
        if { [ -n "${BOTS_EXCLUDED_PROFILE_SOURCE:-}" ] && [ -z "${CLAUDE_PROFILE_DIR:-}" ]; } || \
           { [ -z "${BOTS_EXCLUDED_PROFILE_SOURCE:-}" ] && [ -n "${CLAUDE_PROFILE_DIR:-}" ]; }; then
            log_error "--exclusive-profile-dir and --claude-profile-dir must be provided together"
            return 1
        fi
        if [ -n "${BOTS_EXCLUDED_PROFILE_SOURCE:-}" ] && [ "$BOTS_EXCLUDED_PROFILE_SOURCE" != "$state_excluded_profile" ]; then
            log_error "--exclusive-profile-dir does not match the active hybrid runtime"
            return 1
        fi
        if [ -n "${CLAUDE_PROFILE_DIR:-}" ] && ! hybrid_profile_paths_match "$CLAUDE_PROFILE_DIR" "$state_claude_profile"; then
            log_error "--claude-profile-dir does not match the active hybrid runtime"
            return 1
        fi
        export BOTS_EXCLUDED_PROFILE_SOURCE="$state_excluded_profile"
        export CLAUDE_PROFILE_DIR="$state_claude_profile"
    else
        if [ -n "${BOTS_EXCLUDED_PROFILE_SOURCE:-}" ] || [ -n "${CLAUDE_PROFILE_DIR:-}" ]; then
            log_error "Claude profile options do not match the active OpenClaw-only hybrid runtime"
            return 1
        fi
        unset BOTS_EXCLUDED_PROFILE_SOURCE CLAUDE_PROFILE_DIR
    fi
}

hybrid_apply_model_policy() {
    local config_file primary provider primary_model temporary_file is_glm_model
    config_file="${SINGLEBOX_MODEL_CONFIG_FILE:-}"
    [ -n "$config_file" ] && [ -f "$config_file" ] || {
        log_error "hybrid model policy requires the prepared runtime model config"
        return 1
    }
    primary="$(jq -r '.agents.defaults.model.primary // empty' "$config_file")"
    case "$primary" in
        */*) ;;
        *)
            log_error "hybrid model policy requires a configured OpenClaw primary model"
            return 1
            ;;
    esac
    provider="${primary%%/*}"
    primary_model="${primary#*/}"
    if ! jq -e --arg provider "$provider" '.models.providers[$provider] | type == "object"' "$config_file" >/dev/null; then
        log_error "hybrid model policy cannot find the configured model provider"
        return 1
    fi
    if ! jq -e --arg provider "$provider" --arg model "$primary_model" '
        any(.models.providers[$provider].models[]?; .id == $model)
    ' "$config_file" >/dev/null; then
        log_error "hybrid model policy cannot find the configured primary model"
        return 1
    fi

    is_glm_model=false
    if [ "$(printf '%s' "$primary_model" | tr '[:upper:]' '[:lower:]')" = "glm-5.1" ]; then
        is_glm_model=true
    fi
    temporary_file="${config_file}.hybrid-model-policy.$$"
    if ! (
        umask 077
        jq \
            --arg provider "$provider" \
            --arg primary "$primary" \
            --arg primary_model "$primary_model" \
            --argjson is_glm_model "$is_glm_model" '
              .models.providers[$provider].timeoutSeconds = 600
              | .agents = (if (.agents? | type) == "object" then .agents else {} end)
              | .agents.defaults = (
                  if (.agents.defaults? | type) == "object" then .agents.defaults else {} end
                )
              | .agents.defaults.timeoutSeconds = 600
              | if $is_glm_model then
                  .models.providers[$provider].models |= map(
                    if .id == $primary_model then
                      .reasoning = true
                      | .compat = (
                          (if (.compat? | type) == "object" then .compat else {} end)
                          + {thinkingFormat: "zai"}
                        )
                    else . end
                  )
                  | .agents.defaults.models = (
                      if (.agents.defaults.models? | type) == "object" then .agents.defaults.models else {} end
                    )
                  | .agents.defaults.models[$primary] = (
                      (.agents.defaults.models[$primary] // {})
                      | if type == "object" then . else {} end
                      | .params = (if (.params? | type) == "object" then .params else {} end)
                      | .params.reasoning_effort = "none"
                    )
                else . end
            ' "$config_file" > "$temporary_file"
    ); then
        rm -f "$temporary_file"
        log_error "hybrid model policy failed to write runtime model config"
        return 1
    fi
    if ! chmod 600 "$temporary_file" || ! mv "$temporary_file" "$config_file"; then
        rm -f "$temporary_file"
        log_error "hybrid model policy failed to install runtime model config"
        return 1
    fi

    HYBRID_MODEL_ID="$primary_model"
    export HYBRID_MODEL_ID
    export SINGLEBOX_REQUIRED_OPENCLAW_MODEL="$primary"
    export LLM_FAST_MODEL="$primary_model"
    export LLM_BALANCED_MODEL="$primary_model"
    export LLM_REASONING_MODEL="$primary_model"
    export LLM_LONG_CONTEXT_MODEL="$primary_model"
    export LLM_EXTRACTION_MODEL="$primary_model"
    log_info "Hybrid model policy: primary=${primary}, provider_timeout=600s, agent_timeout=600s, glm_thinking_disabled=${is_glm_model}"
}

hybrid_validate_profiles() {
    [ -n "${BOTS_PROFILE_DIR:-}" ] || {
        log_error "hybrid requires --profile-dir"
        return 1
    }
    if { [ -n "${BOTS_EXCLUDED_PROFILE_SOURCE:-}" ] && [ -z "${CLAUDE_PROFILE_DIR:-}" ]; } || \
       { [ -z "${BOTS_EXCLUDED_PROFILE_SOURCE:-}" ] && [ -n "${CLAUDE_PROFILE_DIR:-}" ]; }; then
        log_error "hybrid requires --exclusive-profile-dir and --claude-profile-dir together; omit both for OpenClaw-only mode"
        return 1
    fi
    bots_dynamic_validate_manifest || return 1

    hybrid_claude_enabled || return 0

    claude_profile_validate_config || return 1
    local claude_source claude_name oc_source oc_name
    claude_source="$(jq -r '.bots[0].source' "$(claude_profile_manifest)")"
    claude_name="$(jq -r '.bots[0].name' "$(claude_profile_manifest)")"
    [ "$BOTS_EXCLUDED_PROFILE_SOURCE" = "$claude_source" ] || {
        log_error "--exclusive-profile-dir must match the Claude profile source: ${claude_source}"
        return 1
    }
    [ "$(jq --arg source "$claude_source" '[.bots[] | select(.source == $source)] | length' "$(bots_dynamic_manifest)")" = 1 ] || {
        log_error "OpenClaw profile must contain exactly one source replaced by the Claude profile: ${claude_source}"
        return 1
    }
    [ "$(bots_dynamic_count)" -ge 1 ] || {
        log_error "hybrid Claude mode requires at least one remaining OpenClaw bot"
        return 1
    }
    while IFS=$'\t' read -r oc_name _ _ oc_source _; do
        if [ "$oc_source" = "$claude_source" ] || [ "$oc_name" = "$claude_name" ]; then
            log_error "hybrid profiles contain a duplicate active identity"
            return 1
        fi
    done < <(bots_dynamic_specs)
}

hybrid_port_preflight() {
    hybrid_claude_enabled || return 0
    local port name pid cwd
    for port_name in "18900:Claude platform-data relay" "28083:BCS Provider bridge"; do
        port="${port_name%%:*}"; name="${port_name#*:}"
        for pid in $(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true); do
            cwd="$(process_cwd "$pid")"
            if [ -z "$cwd" ] || ! path_is_under_dir "$cwd" "$PROJECT_ROOT"; then
                log_error "hybrid blocked: ${name} port ${port} belongs outside this checkout (PID ${pid})"
                return 1
            fi
        done
    done
}

hybrid_prereqs() {
    hybrid_restore_runtime_state || return 1
    hybrid_configure_mode
    hybrid_validate_profiles || return 1
    hybrid_port_preflight
}

hybrid_runtime_prereqs() {
    # Run only after hybrid_setup so a clean checkout can create venvs and
    # build artifacts before service prerequisites inspect them.
    local svc
    check_prereqs_for_services "${HYBRID_START_ORDER[@]}" || return 1
    hybrid_port_preflight
}

hybrid_start() {
    # `start hybrid` is intentionally self-contained after install-tools: setup
    # creates missing venvs, builds artifacts, and prepares runtime config.
    hybrid_restore_runtime_state || return 1
    hybrid_setup || return 1
    hybrid_runtime_prereqs || return 1
    hybrid_clear_runtime_state
    hybrid_save_runtime_state || return 1
    local started=() service rc
    for service in "${HYBRID_START_ORDER[@]}"; do
        log_info ">>> Starting ${service} for hybrid..."
        [ "$service" = frontend ] && export SINGLEBOX_DEFER_FRONTEND_READY_HINT=1
        rc=0; "${service}_start" || rc=$?
        [ "$service" = frontend ] && unset SINGLEBOX_DEFER_FRONTEND_READY_HINT
        if [ "$rc" -ne 0 ]; then
            log_error "hybrid ${service} start failed"
            hybrid_rollback "${started[@]}" && hybrid_clear_runtime_state
            return "$rc"
        fi
        started+=("$service")
    done
    for service in "${HYBRID_START_ORDER[@]}"; do
        if type -t "${service}_ready" >/dev/null && ! "${service}_ready"; then
            log_error "hybrid ${service} is not ready"
            hybrid_rollback "${started[@]}" && hybrid_clear_runtime_state
            return 1
        fi
    done
    print_local_stack_ready_banner
}

hybrid_rollback() {
    local started=("$@") index failed=0
    for ((index=${#started[@]} - 1; index >= 0; index--)); do
        "${started[$index]}_stop" || failed=1
    done
    return "$failed"
}

hybrid_stop() {
    hybrid_restore_runtime_state || return 1
    hybrid_configure_mode
    hybrid_validate_profiles || return 1
    local service failed=0
    for service in "${HYBRID_STOP_ORDER[@]}"; do
        if ! "${service}_stop"; then
            log_warn "hybrid stop failed for ${service}"
            failed=1
        fi
    done
    [ "$failed" -eq 0 ] || return 1
    hybrid_clear_runtime_state
}

hybrid_restart() {
    hybrid_stop && sleep 2 && hybrid_start
}

hybrid_setup() {
    hybrid_configure_mode
    hybrid_validate_profiles || return 1
    hybrid_apply_model_policy || return 1
    local service
    for service in "${HYBRID_SETUP_ORDER[@]}"; do
        log_info ">>> Setting up ${service} for hybrid..."
        if type -t "${service}_setup" >/dev/null && ! "${service}_setup"; then
            log_error "hybrid ${service} setup failed"
            return 1
        fi
    done
}

hybrid_status() {
    hybrid_restore_runtime_state || return 1
    hybrid_configure_mode
    hybrid_validate_profiles || return 1
    if hybrid_claude_enabled; then
        echo "Hybrid status: $(bots_dynamic_count) OpenClaw + 1 Claude Code"
    else
        echo "Hybrid status: $(bots_dynamic_count) OpenClaw"
    fi
    local service
    for service in "${HYBRID_START_ORDER[@]}"; do type -t "${service}_status" >/dev/null && "${service}_status"; done
}

hybrid_help() {
    echo "hybrid - OpenClaw profile stack with optional Claude Code replacement profiles"
}

# Deprecated compatibility aliases; use hybrid.
merchant_hybrid_apply_model_policy() { hybrid_apply_model_policy "$@"; }
merchant_hybrid_validate_profiles() { hybrid_validate_profiles "$@"; }
merchant_hybrid_port_preflight() { hybrid_port_preflight "$@"; }
merchant_hybrid_prereqs() { hybrid_prereqs "$@"; }
merchant_hybrid_start() { hybrid_start "$@"; }
merchant_hybrid_rollback() { hybrid_rollback "$@"; }
merchant_hybrid_stop() { hybrid_stop "$@"; }
merchant_hybrid_restart() { hybrid_restart "$@"; }
merchant_hybrid_setup() { hybrid_setup "$@"; }
merchant_hybrid_status() { hybrid_status "$@"; }
