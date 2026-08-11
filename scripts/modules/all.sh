#!/usr/bin/env bash
# scripts/modules/all.sh — Group module: current all group
[[ -n "${_ALL_SH_LOADED:-}" ]] && return 0
_ALL_SH_LOADED=1

# Canonical service orders — single source of truth for the "all" group.
# toolchain is NOT included here — use `install-tools` command separately.
#
# Full singlebox stack: BAAS/backend create the developer runtime, BCS runs
# collaboration, bots starts the 5 local profiles, demo_bot starts the
# backend-created developer bot, then frontend exposes the workbench. BCN
# onboarding remains an explicit product action.
SETUP_ORDER=(baas backend bcs bcsfuse bots frontend)
START_ORDER=(baas backend bcsfuse bcs bots demo_bot frontend)
STOP_ORDER=(frontend demo_bot bots bcsfuse bcs backend baas)

all_setup() {
    for svc in "${SETUP_ORDER[@]}"; do
        if type -t "${svc}_setup" &>/dev/null; then
            log_info ">>> Setting up ${svc}..."
            "${svc}_setup" || {
                log_error "${svc} setup failed"
                return 1
            }
        else
            log_warn "No setup function for ${svc} — skipping"
        fi
    done
}

all_start() {
    SINGLEBOX_COMMAND=start check_prereqs_for_services ${START_ORDER[*]} || return 1
    mkdir -p "${LOG_DIR}"
    local started_services=()
    local svc

    for svc in "${START_ORDER[@]}"; do
        if type -t "${svc}_start" &>/dev/null; then
            log_info ">>> Starting ${svc}..."
            if [ "$svc" = "frontend" ]; then
                export SINGLEBOX_DEFER_FRONTEND_READY_HINT=1
            fi
            local start_rc=0
            "${svc}_start" || start_rc=$?
            if [ "$svc" = "frontend" ]; then
                unset SINGLEBOX_DEFER_FRONTEND_READY_HINT
            fi
            if [ "$start_rc" -ne 0 ]; then
                log_error "${svc} start failed"
                all_rollback_started_services "${started_services[@]}"
                return "$start_rc"
            fi
            started_services+=("$svc")
        else
            log_warn "No start function for ${svc} — skipping"
        fi
    done

    for svc in "${START_ORDER[@]}"; do
        if type -t "${svc}_ready" &>/dev/null; then
            "${svc}_ready" || {
                log_error "${svc} is not ready after startup"
                all_rollback_started_services "${started_services[@]}"
                return 1
            }
        fi
    done

    print_local_stack_ready_banner
}

all_rollback_started_services() {
    local started=("$@")
    local idx svc
    if [ "${#started[@]}" -eq 0 ]; then
        return 0
    fi

    log_warn "Rolling back started services after startup failure..."
    for ((idx=${#started[@]}-1; idx>=0; idx--)); do
        svc="${started[$idx]}"
        if type -t "${svc}_stop" &>/dev/null; then
            "${svc}_stop" || log_warn "${svc} rollback stop failed, continuing..."
        fi
    done
}

all_stop() {
    # Stop services in reverse start order
    for svc in "${STOP_ORDER[@]}"; do
        if type -t "${svc}_stop" &>/dev/null; then
            "${svc}_stop" || log_warn "${svc} stop failed, continuing..."
        fi
    done
}

all_restart() {
    all_stop
    sleep 2
    all_start
}

all_clean() {
    for svc in "${STOP_ORDER[@]}"; do
        if type -t "${svc}_clean" &>/dev/null; then
            log_info ">>> Cleaning ${svc}..."
            "${svc}_clean" || log_warn "${svc} clean reported warnings, continuing..."
        else
            log_warn "No clean function for ${svc} — skipping"
        fi
    done
}

all_status() {
    echo ""
    echo "Service Status:"
    echo "==============="
    echo ""

    for svc in "${START_ORDER[@]}"; do
        if type -t "${svc}_status" &>/dev/null; then
            "${svc}_status"
        fi
    done

    echo ""
    if [ "${SINGLEBOX_MODE:-standalone}" = "standalone" ]; then
        echo "Mode: STANDALONE (isolated BCS runtime + OpenClaw root)"
        echo "Logs: ${LOG_DIR}"
        echo "BCS runtime: ${STANDALONE_RUNTIME_DIR}"
        echo "BCSFuse runtime: ${BCSFUSE_RUNTIME_DIR}"
        echo "OpenClaw root: ${STANDALONE_OPENCLAW_ROOT}"
        echo "Bot profiles: ${OPENCLAW_PROFILE_ROOT}"
        echo "Bot workspaces: ${OPENCLAW_WORKSPACE_ROOT}"
        echo "Plugin links: ${OPENCLAW_EXTENSIONS_ROOT}"
    else
        echo "Mode: $([ "$LOCAL_MODE" = true ] && echo "LOCAL (BCS SQLite)" || echo "DEV (external MySQL-compatible proxy)")"
        echo "Logs: ${LOG_DIR}"
        echo "Runtime data: ${RUNTIME_DATA_DIR}"
        if [ "$LOCAL_MODE" = true ]; then
            echo "BCS SQLite DB: ${DEP_DIR}/bcs_data/bcs.db"
        fi
        echo "Bot data: ${LOCAL_BOTS_DIR}"
    fi
    echo ""
}

all_help() {
    echo "all - BAAS + Backend + BCS + BCSFuse + 5 local bots + demo bot + frontend stack"
}
