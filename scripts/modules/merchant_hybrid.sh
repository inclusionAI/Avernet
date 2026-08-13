#!/usr/bin/env bash
# scripts/modules/merchant_hybrid.sh — three merchant OpenClaw bots plus Claude data bot
[[ -n "${_MERCHANT_HYBRID_SH_LOADED:-}" ]] && return 0
_MERCHANT_HYBRID_SH_LOADED=1

# The existing merchant OpenClaw profile enables BCSFuse.  Keep that profile
# untouched and start its required sidecar only for this opt-in group.
MERCHANT_HYBRID_START_ORDER=(claude_relays baas backend bcs bcsfuse bots claude_bots bcs_baas_provider frontend)
MERCHANT_HYBRID_STOP_ORDER=(frontend bcs_baas_provider claude_bots bots bcsfuse bcs backend baas claude_relays)
MERCHANT_HYBRID_MODEL_ID="Kimi-K2.6"

merchant_hybrid_apply_model_policy() {
    local config_file primary provider primary_model tmp_file
    config_file="${SINGLEBOX_MODEL_CONFIG_FILE:-}"
    [ -n "$config_file" ] && [ -f "$config_file" ] || {
        log_error "merchant_hybrid model policy requires the prepared runtime model config"
        return 1
    }
    primary="$(jq -r '.agents.defaults.model.primary // empty' "$config_file")"
    case "$primary" in
        */*) ;;
        *)
            log_error "merchant_hybrid model policy requires a configured OpenClaw primary model"
            return 1
            ;;
    esac
    provider="${primary%%/*}"
    primary_model="${primary#*/}"
    if ! jq -e --arg provider "$provider" '.models.providers[$provider] | type == "object"' "$config_file" >/dev/null; then
        log_error "merchant_hybrid model policy cannot find the configured model provider"
        return 1
    fi

    tmp_file="${config_file}.merchant-hybrid.$$.tmp"
    umask 077
    if ! jq --arg provider "$provider" --arg primary_model "$primary_model" --arg model "$MERCHANT_HYBRID_MODEL_ID" '
        .models.providers[$provider].models |= (
          . as $models
          | if any(.[]?; .id == $model) then .
            else (($models | map(select(.id == $primary_model))[0]) // {}) as $template
              | . + [($template + {id: $model, name: $model})]
            end
        )
        | .agents.defaults.model.primary = ($provider + "/" + $model)
        | .agents.defaults.models = ((.agents.defaults.models // {}) + {
            ($provider + "/" + $model): {alias: $model}
          })
    ' "$config_file" > "$tmp_file"; then
        rm -f "$tmp_file"
        log_error "merchant_hybrid model policy failed to update the runtime model config"
        return 1
    fi
    chmod 600 "$tmp_file"
    mv "$tmp_file" "$config_file"

    export SINGLEBOX_REQUIRED_OPENCLAW_MODEL="${provider}/${MERCHANT_HYBRID_MODEL_ID}"
    export LLM_FAST_MODEL="$MERCHANT_HYBRID_MODEL_ID"
    export LLM_BALANCED_MODEL="$MERCHANT_HYBRID_MODEL_ID"
    export LLM_REASONING_MODEL="$MERCHANT_HYBRID_MODEL_ID"
    export LLM_LONG_CONTEXT_MODEL="$MERCHANT_HYBRID_MODEL_ID"
    export LLM_EXTRACTION_MODEL="$MERCHANT_HYBRID_MODEL_ID"
    log_info "merchant_hybrid model policy: OpenClaw, Claude Code, and SOP use ${MERCHANT_HYBRID_MODEL_ID} (provider=${provider})"
}

merchant_hybrid_validate_profiles() {
    [ -n "${BOTS_PROFILE_DIR:-}" ] && [ -n "${BOTS_EXCLUDED_PROFILE_SOURCE:-}" ] && [ -n "${CLAUDE_PROFILE_DIR:-}" ] || {
        log_error "merchant_hybrid requires --profile-dir, --exclusive-profile-dir, and --claude-profile-dir"
        return 1
    }
    [ "$BOTS_EXCLUDED_PROFILE_SOURCE" = platform-data ] || { log_error "merchant_hybrid only supports --exclusive-profile-dir platform-data"; return 1; }
    bots_dynamic_validate_manifest || return 1
    [ "$(jq '[.bots[] | select(.source == "platform-data")] | length' "$(bots_dynamic_manifest)")" = 1 ] || {
        log_error "OpenClaw profile must contain exactly one platform-data source"
        return 1
    }
    [ "$(bots_dynamic_count)" = 3 ] || { log_error "merchant_hybrid must leave exactly three OpenClaw bots"; return 1; }
    claude_profile_validate_config || return 1
    local claude_source claude_name oc_source oc_name
    claude_source="$(jq -r '.bots[0].source' "$(claude_profile_manifest)")"
    claude_name="$(jq -r '.bots[0].name' "$(claude_profile_manifest)")"
    while IFS=$'\t' read -r oc_name _ _ oc_source _; do
        if [ "$oc_source" = "$claude_source" ] || [ "$oc_name" = "$claude_name" ]; then
            log_error "merchant_hybrid profiles contain a duplicate active identity"
            return 1
        fi
    done < <(bots_dynamic_specs)
}

merchant_hybrid_port_preflight() {
    local port name pid cwd
    for port_name in "18900:Claude platform-data relay" "28083:BCS Provider bridge"; do
        port="${port_name%%:*}"; name="${port_name#*:}"
        for pid in $(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true); do
            cwd="$(process_cwd "$pid")"
            if [ -z "$cwd" ] || ! path_is_under_dir "$cwd" "$PROJECT_ROOT"; then
                log_error "merchant_hybrid blocked: ${name} port ${port} belongs outside this checkout (PID ${pid})"
                return 1
            fi
        done
    done
}

merchant_hybrid_prereqs() {
    # `check_prereqs_for_services` uses the conventional `svc` loop variable.
    # Keep it local here so Bash dynamic scoping cannot overwrite
    # `start_service`'s target and accidentally dispatch only its last service.
    local svc
    merchant_hybrid_validate_profiles || return 1
    check_prereqs_for_services "${MERCHANT_HYBRID_START_ORDER[@]}" || return 1
    merchant_hybrid_port_preflight
}

merchant_hybrid_start() {
    merchant_hybrid_apply_model_policy || return 1
    merchant_hybrid_prereqs || return 1
    export MERCHANT_HYBRID_ACTIVE=1
    local started=() service rc
    for service in "${MERCHANT_HYBRID_START_ORDER[@]}"; do
        log_info ">>> Starting ${service} for merchant_hybrid..."
        [ "$service" = frontend ] && export SINGLEBOX_DEFER_FRONTEND_READY_HINT=1
        rc=0; "${service}_start" || rc=$?
        [ "$service" = frontend ] && unset SINGLEBOX_DEFER_FRONTEND_READY_HINT
        if [ "$rc" -ne 0 ]; then
            log_error "merchant_hybrid ${service} start failed"
            merchant_hybrid_rollback "${started[@]}"
            return "$rc"
        fi
        started+=("$service")
    done
    for service in "${MERCHANT_HYBRID_START_ORDER[@]}"; do
        if type -t "${service}_ready" >/dev/null && ! "${service}_ready"; then
            log_error "merchant_hybrid ${service} is not ready"
            merchant_hybrid_rollback "${started[@]}"
            return 1
        fi
    done
    print_local_stack_ready_banner
}

merchant_hybrid_rollback() {
    local started=("$@") index
    for ((index=${#started[@]} - 1; index >= 0; index--)); do "${started[$index]}_stop" || true; done
}

merchant_hybrid_stop() {
    export MERCHANT_HYBRID_ACTIVE=1
    merchant_hybrid_validate_profiles || return 1
    local service
    for service in "${MERCHANT_HYBRID_STOP_ORDER[@]}"; do "${service}_stop" || log_warn "merchant_hybrid stop failed for ${service}"; done
}

merchant_hybrid_restart() {
    merchant_hybrid_stop && sleep 2 && merchant_hybrid_start
}

merchant_hybrid_setup() {
    merchant_hybrid_apply_model_policy || return 1
    merchant_hybrid_validate_profiles || return 1
    local service
    for service in "${MERCHANT_HYBRID_START_ORDER[@]}"; do
        if type -t "${service}_setup" >/dev/null && ! "${service}_setup"; then
            log_error "merchant_hybrid ${service} setup failed"
            return 1
        fi
    done
}

merchant_hybrid_status() {
    merchant_hybrid_validate_profiles || return 1
    echo "Merchant hybrid status: 3 OpenClaw + 1 Claude Code"
    local service
    for service in "${MERCHANT_HYBRID_START_ORDER[@]}"; do type -t "${service}_status" >/dev/null && "${service}_status"; done
}

merchant_hybrid_help() {
    echo "merchant_hybrid - 3 merchant OpenClaw bots plus one Claude Code platform-data Provider bot"
}
