#!/usr/bin/env bash
# scripts/modules/bcs_frontend.sh — Composite module: BCS + frontend with LOCAL_MODE handling
[[ -n "${_BCS_FRONTEND_SH_LOADED:-}" ]] && return 0
_BCS_FRONTEND_SH_LOADED=1

bcs_frontend_setup() {
    if [ "$LOCAL_MODE" = true ]; then
        bcs_bots_setup || return 1
    else
        bcs_setup || return 1
    fi
    frontend_setup || return 1
}

bcs_frontend_start() {
    if [ "$LOCAL_MODE" != true ]; then
        log_info "Non-local BCS mode expects required external proxies to be started by the caller"
    else
        log_info "Using local BCS mode; no external proxy bootstrap required"
    fi
    mkdir -p "${LOG_DIR}"
    resolve_bcs_server_env

    if [ "$LOCAL_MODE" = true ]; then
        bcs_bots_start || return 1
    else
        bcs_start || return 1
    fi

    # Set frontend dev script based on LOCAL_MODE
    local old_frontend_script="${FRONTEND_DEV_SCRIPT:-}"
    if [ "$LOCAL_MODE" = true ]; then
        export FRONTEND_DEV_SCRIPT=devs:local:oss
    else
        export FRONTEND_DEV_SCRIPT=devs:dev
    fi

    export SINGLEBOX_DEFER_FRONTEND_READY_HINT=1
    local frontend_start_rc=0
    frontend_start || frontend_start_rc=$?
    unset SINGLEBOX_DEFER_FRONTEND_READY_HINT

    # Restore original FRONTEND_DEV_SCRIPT
    if [ -n "$old_frontend_script" ]; then
        export FRONTEND_DEV_SCRIPT="$old_frontend_script"
    else
        unset FRONTEND_DEV_SCRIPT
    fi
    if [ "$frontend_start_rc" -ne 0 ]; then
        return "$frontend_start_rc"
    fi

    if [ "$LOCAL_MODE" = true ]; then
        bcs_bots_ready || {
            log_error "BCS + 5 local bots are not ready after startup"
            return 1
        }
    else
        bcs_ready || {
            log_error "BCS is not ready after startup"
            return 1
        }
    fi
    frontend_ready || {
        log_error "frontend is not ready after startup"
        return 1
    }
    if [ "$LOCAL_MODE" = true ]; then
        print_local_stack_ready_banner
    else
        print_local_stack_ready_banner "BCS + frontend startup checks passed"
    fi
}

bcs_frontend_stop() {
    frontend_stop
    if [ "$LOCAL_MODE" = true ]; then
        bcs_bots_stop
    else
        bcs_stop
    fi
}

bcs_frontend_restart() {
    bcs_frontend_stop
    sleep 2
    bcs_frontend_start
}

bcs_frontend_clean() {
    if [ "$LOCAL_MODE" = true ]; then
        bcs_bots_clean
    else
        bcs_clean
    fi
}

bcs_frontend_status() {
    if [ "$LOCAL_MODE" = true ]; then
        bcs_bots_status
    else
        bcs_status
    fi
    frontend_status
}

bcs_frontend_help() {
    echo "bcs_frontend - BCS + BCN frontend E2E shortcut (ports ${BCS_PORT}, ${FRONTEND_PORT:-8000})"
    if [ "$LOCAL_MODE" = true ]; then
        echo "  local: bcs_bots + frontend devs:local:oss"
    else
        echo "  dev:   BCS dev env + skip default onboard + frontend devs:dev"
    fi
}
