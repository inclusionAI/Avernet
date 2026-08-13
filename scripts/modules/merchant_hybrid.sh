#!/usr/bin/env bash
# scripts/modules/merchant_hybrid.sh — 3 OpenClaw merchant bots plus one Claude bot
[[ -n "${_MERCHANT_HYBRID_SH_LOADED:-}" ]] && return 0
_MERCHANT_HYBRID_SH_LOADED=1

MERCHANT_HYBRID_START_ORDER=(claude_relays baas backend bcs bots claude_bots bcs_baas_provider frontend)
MERCHANT_HYBRID_STOP_ORDER=(frontend bcs_baas_provider claude_bots bots bcs backend baas claude_relays)

merchant_hybrid_validate_combined_identities() {
    local claude_source claude_profile claude_name claude_port
    IFS=$'\t' read -r claude_source claude_profile claude_name claude_port < <(
        jq -r '.bots[0] | [.source, .profile, .name, (.runtime.relay_port | tostring)] | @tsv' \
            "$(claude_profile_manifest)"
    )

    local name profile port source summary domains skills scopes runtime collision=''
    while IFS=$'\t' read -r name profile port source summary domains skills scopes runtime; do
        if [ "$source" = "$claude_source" ] || [ "$profile" = "$claude_profile" ] || [ "$name" = "$claude_name" ] || [ "$port" = "$claude_port" ]; then
            collision=1
            break
        fi
    done < <(bots_dynamic_specs)
    if [ -n "$collision" ]; then
        log_error "merchant_hybrid OpenClaw and Claude profiles contain a duplicate active identity or relay port"
        return 1
    fi
}

merchant_hybrid_legacy_excluded_openclaw_port() {
    jq -r '
      . as $root
      | ($root.port_start // 0 | tonumber) as $start
      | ($root.port_step // 1 | tonumber) as $step
      | $root.bots
      | to_entries[]
      | select(.value.source == "platform-data")
      | ($start + (.key * $step))
    ' "$(bots_dynamic_manifest)"
}

merchant_hybrid_require_no_legacy_excluded_bot() {
    local port pids
    port="$(merchant_hybrid_legacy_excluded_openclaw_port)" || return 1
    pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
    [ -z "$pids" ] && return 0
    log_error "merchant_hybrid found a listener on the excluded legacy platform-data OpenClaw port ${port}"
    log_error "Stop or clean the old four-bot profile before migration; the Claude platform-data bot must be the only active platform-data identity"
    return 1
}

merchant_hybrid_validate_profiles() {
    if [ -n "${CLAUDE_BOTS_CONFIG:-}" ]; then
        log_error "merchant_hybrid cannot be combined with --claude-bots-config"
        return 1
    fi
    if [ -z "${BOTS_PROFILE_DIR:-}" ] || [ -z "${BOTS_EXCLUDED_PROFILE_SOURCE:-}" ] || [ -z "${CLAUDE_PROFILE_DIR:-}" ]; then
        log_error "merchant_hybrid requires --profile-dir, --exclusive-profile-dir, and --claude-profile-dir"
        return 1
    fi
    if [ "${BOTS_EXCLUDED_PROFILE_SOURCE}" != "platform-data" ]; then
        log_error "merchant_hybrid only supports --exclusive-profile-dir platform-data"
        return 1
    fi
    if ! jq -e '[.bots[] | select(.source == "platform-data")] | length == 1' "$(bots_dynamic_manifest)" >/dev/null 2>&1; then
        log_error "OpenClaw profile must contain exactly one platform-data source before exclusion"
        return 1
    fi
    bots_dynamic_validate_manifest || return 1
    if [ "$(bots_dynamic_count)" != "3" ]; then
        log_error "merchant_hybrid must leave exactly three OpenClaw bots after excluding platform-data"
        return 1
    fi
    claude_bots_validate_config || return 1
    merchant_hybrid_validate_combined_identities
}

merchant_hybrid_port_is_available_or_owned() {
    local port="$1" service_name="$2" pids pid cwd
    pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
    [ -z "$pids" ] && return 0
    for pid in $pids; do
        cwd="$(process_cwd "$pid")"
        if [ -z "$cwd" ] || ! path_is_under_dir "$cwd" "$PROJECT_ROOT"; then
            log_error "merchant_hybrid preflight blocked: ${service_name} port ${port} is owned outside this checkout (PID ${pid}, cwd=${cwd:-unknown})"
            return 1
        fi
    done
}

merchant_hybrid_port_preflight() {
    merchant_hybrid_require_no_legacy_excluded_bot || return 1
    merchant_hybrid_port_is_available_or_owned 18913 "Claude platform-data relay" || return 1
    merchant_hybrid_port_is_available_or_owned 20003 "Claude engine adapter" || return 1
    merchant_hybrid_port_is_available_or_owned 8890 "BAAS" || return 1
    merchant_hybrid_port_is_available_or_owned 8888 "Backend" || return 1
    merchant_hybrid_port_is_available_or_owned "${BCS_PORT}" "BCS" || return 1
    merchant_hybrid_port_is_available_or_owned "${BCS_BAAS_PROVIDER_PORT}" "BCS Provider bridge" || return 1
    merchant_hybrid_port_is_available_or_owned "${FRONTEND_PORT}" "Frontend" || return 1
}

merchant_hybrid_prereqs() {
    merchant_hybrid_validate_profiles || return 1
    check_prereqs_for_services "${MERCHANT_HYBRID_START_ORDER[@]}" || return 1
    merchant_hybrid_port_preflight
}

merchant_hybrid_start() {
    merchant_hybrid_prereqs || return 1
    local started_services=() svc start_rc
    for svc in "${MERCHANT_HYBRID_START_ORDER[@]}"; do
        log_info ">>> Starting ${svc} for merchant_hybrid..."
        if [ "$svc" = "frontend" ]; then export SINGLEBOX_DEFER_FRONTEND_READY_HINT=1; fi
        start_rc=0
        "${svc}_start" || start_rc=$?
        if [ "$svc" = "frontend" ]; then unset SINGLEBOX_DEFER_FRONTEND_READY_HINT; fi
        if [ "$start_rc" -ne 0 ]; then
            log_error "merchant_hybrid ${svc} start failed"
            merchant_hybrid_rollback_started_services "${started_services[@]}"
            return "$start_rc"
        fi
        started_services+=("$svc")
    done
    for svc in "${MERCHANT_HYBRID_START_ORDER[@]}"; do
        if type -t "${svc}_ready" &>/dev/null && ! "${svc}_ready"; then
            log_error "merchant_hybrid ${svc} is not ready after startup"
            merchant_hybrid_rollback_started_services "${started_services[@]}"
            return 1
        fi
    done
    print_local_stack_ready_banner
}

merchant_hybrid_rollback_started_services() {
    local started=("$@") idx svc
    [ "${#started[@]}" -gt 0 ] || return 0
    log_warn "Rolling back merchant_hybrid services after startup failure..."
    for ((idx=${#started[@]}-1; idx>=0; idx--)); do
        svc="${started[$idx]}"
        "${svc}_stop" || log_warn "merchant_hybrid rollback failed for ${svc}"
    done
}

merchant_hybrid_stop() {
    merchant_hybrid_validate_profiles || return 1
    local svc
    for svc in "${MERCHANT_HYBRID_STOP_ORDER[@]}"; do
        "${svc}_stop" || log_warn "merchant_hybrid stop failed for ${svc}"
    done
}

merchant_hybrid_restart() {
    local previous_allow_owned_ports="${BOTS_ALLOW_OWNED_PORTS_FOR_RESTART:-}"
    export BOTS_ALLOW_OWNED_PORTS_FOR_RESTART=1
    if ! merchant_hybrid_prereqs; then
        if [ -n "$previous_allow_owned_ports" ]; then
            export BOTS_ALLOW_OWNED_PORTS_FOR_RESTART="$previous_allow_owned_ports"
        else
            unset BOTS_ALLOW_OWNED_PORTS_FOR_RESTART
        fi
        return 1
    fi
    if [ -n "$previous_allow_owned_ports" ]; then
        export BOTS_ALLOW_OWNED_PORTS_FOR_RESTART="$previous_allow_owned_ports"
    else
        unset BOTS_ALLOW_OWNED_PORTS_FOR_RESTART
    fi
    merchant_hybrid_stop || return 1
    sleep 2
    merchant_hybrid_start
}

merchant_hybrid_setup() {
    merchant_hybrid_validate_profiles || return 1
    local svc
    for svc in "${MERCHANT_HYBRID_START_ORDER[@]}"; do
        if type -t "${svc}_setup" &>/dev/null; then "${svc}_setup" || return 1; fi
    done
}

merchant_hybrid_status() {
    merchant_hybrid_validate_profiles || return 1
    echo ""
    echo "Merchant hybrid status: 3 OpenClaw + 1 Claude Code"
    local svc
    for svc in "${MERCHANT_HYBRID_START_ORDER[@]}"; do
        if type -t "${svc}_status" &>/dev/null; then "${svc}_status"; fi
    done
}

merchant_hybrid_help() {
    echo "merchant_hybrid - 3 merchant OpenClaw bots plus one platform-data Claude Code Provider bot"
}
