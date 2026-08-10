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
DEFAULT_SETUP_ORDER=(baas backend bcs bcsfuse bots frontend)
DEFAULT_START_ORDER=(baas backend bcs bcsfuse bots demo_bot frontend)
DEFAULT_STOP_ORDER=(frontend demo_bot bots bcsfuse bcs backend baas)
MIXED_SETUP_ORDER=(claude_relays baas backend bcs bcsfuse bots claude_bots bcs_baas_provider frontend)
MIXED_START_ORDER=(claude_relays baas backend bcs bcsfuse bots claude_bots bcs_baas_provider frontend)
MIXED_STOP_ORDER=(frontend bcs_baas_provider claude_bots bots bcsfuse bcs backend baas claude_relays)
SETUP_ORDER=("${DEFAULT_SETUP_ORDER[@]}")
START_ORDER=("${DEFAULT_START_ORDER[@]}")
STOP_ORDER=("${DEFAULT_STOP_ORDER[@]}")

all_select_topology() {
    if type -t claude_bots_enabled &>/dev/null && claude_bots_enabled; then
        SETUP_ORDER=("${MIXED_SETUP_ORDER[@]}")
        START_ORDER=("${MIXED_START_ORDER[@]}")
        STOP_ORDER=("${MIXED_STOP_ORDER[@]}")
        return 0
    fi
    SETUP_ORDER=("${DEFAULT_SETUP_ORDER[@]}")
    START_ORDER=("${DEFAULT_START_ORDER[@]}")
    STOP_ORDER=("${DEFAULT_STOP_ORDER[@]}")
}

# A mixed stack owns the complete local collaboration topology.  Do not let a
# restart tear down its owned processes only to fail later because another
# checkout already owns one of the required ports.
all_mixed_port_is_available_or_owned() {
    local port="$1"
    local service_name="$2"
    local pids pid cwd

    pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
    [ -z "$pids" ] && return 0

    for pid in $pids; do
        cwd="$(process_cwd "$pid")"
        if [ -z "$cwd" ] || ! path_is_under_dir "$cwd" "$PROJECT_ROOT"; then
            log_error "Mixed stack preflight blocked: ${service_name} port ${port} is owned outside this checkout (PID ${pid}, cwd=${cwd:-unknown})."
            log_error "Stop the external stack before retrying; no services from this checkout were stopped."
            return 1
        fi
    done
}

all_mixed_port_ownership_preflight() {
    if ! type -t claude_bots_enabled &>/dev/null || ! claude_bots_enabled; then
        return 0
    fi

    # Relay ports are fixed by the strict mixed-config schema.  The remaining
    # ports are the listeners managed by this all topology.
    all_mixed_port_is_available_or_owned 18910 "Claude planner relay" || return 1
    all_mixed_port_is_available_or_owned 18911 "Claude developer relay" || return 1
    all_mixed_port_is_available_or_owned 18912 "Claude reviewer relay" || return 1
    all_mixed_port_is_available_or_owned 20003 "engine adapter" || return 1
    all_mixed_port_is_available_or_owned 8890 "BAAS" || return 1
    all_mixed_port_is_available_or_owned 8888 "Backend" || return 1
    all_mixed_port_is_available_or_owned "${BCS_PORT}" "BCS" || return 1
    all_mixed_port_is_available_or_owned "${BCSFUSE_PORT}" "BCSFuse" || return 1
    all_mixed_port_is_available_or_owned "${BCS_BAAS_PROVIDER_PORT}" "BCS Provider bridge" || return 1
    all_mixed_port_is_available_or_owned "${FRONTEND_PORT}" "Frontend" || return 1
}

all_preflight() {
    all_select_topology
    SINGLEBOX_COMMAND="${1:-start}" check_prereqs_for_services "${START_ORDER[@]}" || return 1
    all_mixed_port_ownership_preflight
}

all_setup() {
    all_select_topology
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
    all_select_topology
    all_preflight start || return 1
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
    all_select_topology
    # Stop services in reverse start order
    for svc in "${STOP_ORDER[@]}"; do
        if type -t "${svc}_stop" &>/dev/null; then
            "${svc}_stop" || log_warn "${svc} stop failed, continuing..."
        fi
    done
}

all_restart() {
    all_select_topology
    # Never stop a usable local stack before confirming the replacement can
    # start. This is especially important for a mixed worktree sharing the
    # default ports with another checkout.
    all_preflight restart || return 1
    all_stop

    # Keep the all-group restart atomic. The top-level dispatcher must not
    # reproduce this lifecycle service-by-service, otherwise a partial
    # dispatch can leave only the frontend listener running and still print
    # its module-local ready banner.
    local mock_started_by_command=0
    if type -t singlebox_mock_model_stop_required_for_services &>/dev/null \
        && singlebox_mock_model_stop_required_for_services all; then
        singlebox_mock_model_stop || return 1
    fi
    sleep 2
    if type -t singlebox_mock_model_start &>/dev/null; then
        singlebox_mock_model_start || return 1
        mock_started_by_command="${SINGLEBOX_MOCK_MODEL_STARTED_BY_COMMAND:-0}"
    fi
    if ! all_start; then
        if [ "$mock_started_by_command" = "1" ]; then
            singlebox_mock_model_stop || log_warn "Failed to roll back the mock model server."
        fi
        return 1
    fi
}

all_clean() {
    all_select_topology
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
    all_select_topology
    echo ""
    echo "Service Status:"
    echo "==============="
    echo ""
    if type -t claude_bots_enabled &>/dev/null && claude_bots_enabled; then
        echo "Topology: 5 OpenClaw bots + 3 Claude Code Provider bots"
        echo ""
    fi

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
    echo "all - default: BAAS + Backend + BCS + BCSFuse + 5 local bots + demo bot + frontend"
    echo "      --claude-bots-config: 5 OpenClaw bots + 3 Claude Code Provider bots + frontend"
}
