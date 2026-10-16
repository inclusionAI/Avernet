#!/usr/bin/env bash
# singlebox/modules/bbs_demo.sh — BBS demo stack without the default 5 profile bots
[[ -n "${_BBS_DEMO_SH_LOADED:-}" ]] && return 0
_BBS_DEMO_SH_LOADED=1

# Keep the same service dependencies as the full stack, but omit the local
# `bots` module. The two community bots are created through Backend/BaaS in
# `demo_bot`; they do not use the five local profile gateways.
BBS_DEMO_SETUP_ORDER=(baas backend bcs bcsfuse gateway frontend)
BBS_DEMO_START_ORDER=(baas backend bcsfuse bcs gateway demo_bot frontend)
BBS_DEMO_STOP_ORDER=(frontend demo_bot gateway bcs bcsfuse backend baas)

bbs_demo_setup() {
    local svc
    for svc in "${BBS_DEMO_SETUP_ORDER[@]}"; do
        if type -t "${svc}_setup" &>/dev/null; then
            log_info ">>> Setting up ${svc}..."
            if ! "${svc}_setup"; then
                log_error "${svc} setup failed"
                return 1
            fi
        else
            log_warn "No setup function for ${svc} — skipping"
        fi
    done
}

bbs_demo_rollback_started_services() {
    local started=("$@")
    local idx svc
    if [ "${#started[@]}" -eq 0 ]; then
        return 0
    fi

    log_warn "Rolling back started BBS demo services after startup failure..."
    for ((idx=${#started[@]}-1; idx>=0; idx--)); do
        svc="${started[$idx]}"
        if type -t "${svc}_stop" &>/dev/null; then
            "${svc}_stop" || log_warn "${svc} rollback stop failed, continuing..."
        fi
    done
}

bbs_demo_start() {
    mkdir -p "${LOG_DIR}"

    local started_services=()
    local svc start_rc
    for svc in "${BBS_DEMO_START_ORDER[@]}"; do
        if ! type -t "${svc}_start" &>/dev/null; then
            log_error "Unknown BBS demo service or no start: ${svc}"
            bbs_demo_rollback_started_services "${started_services[@]}"
            return 1
        fi

        log_info ">>> Starting ${svc}..."
        if [ "$svc" = frontend ]; then
            export SINGLEBOX_DEFER_FRONTEND_READY_HINT=1
        fi
        start_rc=0
        "${svc}_start" || start_rc=$?
        if [ "$svc" = frontend ]; then
            unset SINGLEBOX_DEFER_FRONTEND_READY_HINT
        fi
        if [ "$start_rc" -ne 0 ]; then
            log_error "${svc} start failed"
            bbs_demo_rollback_started_services "${started_services[@]}"
            return "$start_rc"
        fi
        started_services+=("$svc")
    done

    for svc in "${BBS_DEMO_START_ORDER[@]}"; do
        if type -t "${svc}_ready" &>/dev/null; then
            if ! "${svc}_ready"; then
                log_error "${svc} is not ready after startup"
                bbs_demo_rollback_started_services "${started_services[@]}"
                return 1
            fi
        fi
    done

    log_info "BBS demo stack ready"
    log_info "Workbench: http://127.0.0.1:${FRONTEND_PORT:-8000}/"
    log_info "Demo format: Backend creates exactly the two community bots during demo_bot initialization"
}

bbs_demo_stop() {
    local svc
    for svc in "${BBS_DEMO_STOP_ORDER[@]}"; do
        if type -t "${svc}_stop" &>/dev/null; then
            "${svc}_stop" || log_warn "${svc} stop failed, continuing..."
        else
            log_warn "No stop function for ${svc} — skipping"
        fi
    done
}

bbs_demo_restart() {
    bbs_demo_stop
    sleep 2
    bbs_demo_start
}

bbs_demo_clean() {
    local svc
    for svc in "${BBS_DEMO_STOP_ORDER[@]}"; do
        if type -t "${svc}_clean" &>/dev/null; then
            log_info ">>> Cleaning ${svc}..."
            "${svc}_clean" || log_warn "${svc} clean reported warnings, continuing..."
        else
            log_warn "No clean function for ${svc} — skipping"
        fi
    done
}

bbs_demo_status() {
    local svc
    echo ""
    echo "Service Status:"
    echo "==============="
    echo ""
    for svc in "${BBS_DEMO_START_ORDER[@]}"; do
        if type -t "${svc}_status" &>/dev/null; then
            "${svc}_status"
        else
            log_warn "No status function for ${svc}"
        fi
    done
    echo ""
}

bbs_demo_prereqs() {
    SINGLEBOX_COMMAND=start check_prereqs_for_services "${BBS_DEMO_START_ORDER[@]}"
}

bbs_demo_help() {
    echo "bbs_demo - BAAS + Backend + BCSFuse + BCS + Gateway + two-bot BBS demo + frontend (no 5 local profile bots)"
}
