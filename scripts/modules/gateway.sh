#!/usr/bin/env bash
# scripts/modules/gateway.sh — Gateway service module
[[ -n "${_GATEWAY_SH_LOADED:-}" ]] && return 0
_GATEWAY_SH_LOADED=1

# Service-specific constants
GATEWAY_LOG="${LOG_DIR}/gateway.log"
GATEWAY_PID_FILE="${DEP_DIR}/gateway.pid"
GATEWAY_APP_SCRIPT="${GATEWAY_DIR}/scripts/app.sh"
GATEWAY_PORT="${GATEWAY_PORT:-8889}"


gateway_setup() {
    if ! check_directory_exists "${GATEWAY_DIR}" "gateway"; then
        return 1
    fi

    check_uv_installed || { log_error "uv not found. Run: singlebox.sh install-tools"; return 1; }

    cd "${GATEWAY_DIR}"
    log_info "Syncing Python dependencies for Gateway..."
    if ! uv sync --python ">=3.12,<3.13" --index-url "${PYPI_INDEX_URL}"; then
        log_error "Failed to sync Python dependencies for Gateway"
        return 1
    fi
    log_info "Gateway dependencies synced successfully"
}


gateway_start() {
    mkdir -p "${LOG_DIR}"

    if ! check_directory_exists "${GATEWAY_DIR}" "gateway"; then
        return 1
    fi
    if [ ! -x "${GATEWAY_APP_SCRIPT}" ]; then
        log_error "Gateway lifecycle script not executable: ${GATEWAY_APP_SCRIPT}"
        return 1
    fi

    gateway_setup || return 1

    stop_port_processes_if_owned "${GATEWAY_PORT}" "${GATEWAY_DIR}" "existing gateway"
    stop_matching_processes_if_owned "gateway/community/main.py" "${GATEWAY_DIR}" "existing gateway process"
    require_port_available_after_owned_stop "${GATEWAY_PORT}" "gateway" "set GATEWAY_PORT=<free-port> in .env.local or pass --gateway-port <free-port>" || return 1

    log_info "Starting Gateway service on port ${GATEWAY_PORT}..."
    log_info "Log: ${GATEWAY_LOG}"

    (
        cd "${GATEWAY_DIR}"
        GATEWAY_PORT="${GATEWAY_PORT}" APP_PORT="${GATEWAY_PORT}" "${GATEWAY_APP_SCRIPT}" start --mode bare
    ) >> "${GATEWAY_LOG}" 2>&1

    local gateway_pid=""
    if [ -f "${GATEWAY_DIR}/tmp/app.pid" ]; then
        gateway_pid="$(cat "${GATEWAY_DIR}/tmp/app.pid" 2>/dev/null || true)"
        if [ -n "${gateway_pid}" ]; then
            echo "${gateway_pid}" > "${GATEWAY_PID_FILE}"
        fi
    fi

    if gateway_ready; then
        log_info "Gateway started successfully${gateway_pid:+ (PID: ${gateway_pid})}"
        log_info "Gateway health: http://127.0.0.1:${GATEWAY_PORT}/health"
        return 0
    fi

    log_error "Gateway failed to become ready; check ${GATEWAY_LOG} and ${GATEWAY_DIR}/tmp/app.log"
    return 1
}


gateway_stop() {
    log_info "Stopping Gateway..."

    if [ -x "${GATEWAY_APP_SCRIPT}" ]; then
        (cd "${GATEWAY_DIR}" && GATEWAY_PORT="${GATEWAY_PORT}" APP_PORT="${GATEWAY_PORT}" "${GATEWAY_APP_SCRIPT}" stop) >> "${GATEWAY_LOG}" 2>&1 || true
    fi

    if [ -f "${GATEWAY_PID_FILE}" ]; then
        local gateway_pid
        gateway_pid="$(cat "${GATEWAY_PID_FILE}" 2>/dev/null || true)"
        if [ -n "${gateway_pid}" ]; then
            stop_process_if_owned "${gateway_pid}" "${GATEWAY_DIR}" "gateway pidfile process" || true
        fi
        rm -f "${GATEWAY_PID_FILE}"
    fi

    stop_port_processes_if_owned "${GATEWAY_PORT}" "${GATEWAY_DIR}" "gateway"
    stop_matching_processes_if_owned "gateway/community/main.py" "${GATEWAY_DIR}" "gateway process"
    log_info "Gateway stopped"
}


gateway_status() {
    local gateway_pid=""
    gateway_pid="$(lsof -tiTCP:${GATEWAY_PORT} -sTCP:LISTEN 2>/dev/null | head -1 || true)"
    if [ -n "${gateway_pid}" ]; then
        if gateway_ready; then
            echo "  Gateway:   Running (PID: ${gateway_pid}, port: ${GATEWAY_PORT}, health: OK)"
        else
            echo "  Gateway:   Running (PID: ${gateway_pid}, port: ${GATEWAY_PORT}, health: FAIL)"
        fi
    else
        echo "  Gateway:   Stopped (port: ${GATEWAY_PORT})"
    fi
}


gateway_ready() {
    curl --noproxy '*' --connect-timeout 1 --max-time 2 -s "http://127.0.0.1:${GATEWAY_PORT}/health" > /dev/null 2>&1
}


gateway_prereqs() {
    local has_error=false

    echo -e "${CYAN}[gateway] Prerequisites${NC}"

    if check_uv_installed; then
        prereq_ok "uv: $(uv --version 2>&1 | head -1)"
    else
        prereq_error "uv not found. Run: singlebox.sh install-tools"
        has_error=true
    fi

    if check_directory_exists "${GATEWAY_DIR}" "gateway" 2>/dev/null; then
        prereq_ok "directory: ${GATEWAY_DIR}"
    else
        prereq_error "directory not found: ${GATEWAY_DIR}"
        has_error=true
    fi

    if [ -x "${GATEWAY_APP_SCRIPT}" ]; then
        prereq_ok "lifecycle script: ${GATEWAY_APP_SCRIPT}"
    else
        prereq_error "lifecycle script not executable: ${GATEWAY_APP_SCRIPT}"
        has_error=true
    fi

    if [ -f "${GATEWAY_DIR}/.venv/bin/activate" ]; then
        prereq_ok "venv: ${GATEWAY_DIR}/.venv"
    else
        prereq_warn "venv not found: ${GATEWAY_DIR}/.venv — run: singlebox.sh setup gateway"
    fi

    if check_port_available "${GATEWAY_PORT}"; then
        prereq_ok "Port ${GATEWAY_PORT} available"
    else
        prereq_warn "Port ${GATEWAY_PORT} is in use"
    fi

    if [ "${has_error}" = true ]; then
        return 1
    fi
    return 0
}


gateway_help() {
    echo "gateway - Gateway service (port ${GATEWAY_PORT})"
}
