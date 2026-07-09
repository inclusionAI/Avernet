#!/usr/bin/env bash
# scripts/modules/engine.sh — OpenClawEnterprise engine adapter module
[[ -n "${_ENGINE_SH_LOADED:-}" ]] && return 0
_ENGINE_SH_LOADED=1

# Service-specific constants
DEFAULT_ENGINE="openclaw"
ENGINE_STATE_FILE="${LOG_DIR}/.engine_type"

engine_setup() {
    log_info "Setting up OpenClawEnterprise engine..."

    if ! check_directory_exists "${ENGINE_DIR}" "engine"; then
        return 1
    fi

    cd "${ENGINE_DIR}"

    # Check uv is installed
    if ! command -v uv &> /dev/null; then
        log_error "uv not found. Run: $0 setup"
        return 1
    fi

    # Sync dependencies
    log_info "Syncing engine dependencies with uv..."
    if ! uv sync --index-url "${PYPI_INDEX_URL}"; then
        log_error "Failed to sync engine dependencies"
        return 1
    fi
    log_info "Engine dependencies synced successfully"
}

engine_start() {
    mkdir -p "${LOG_DIR}"

    stop_port_processes_if_owned 20003 "${PROJECT_ROOT}" "existing engine"
    stop_matching_processes_if_owned "engine.community.api.app" "${PROJECT_ROOT}" "existing engine process"

    # Check start script exists
    local run_script="${ENGINE_DIR}/scripts/run.sh"
    if [ ! -f "$run_script" ]; then
        log_error "Adapter start script not found: ${run_script}"
        return 1
    fi

    log_info "Starting Engine adapter on port 20003 with engine: ${CHAT_ENGINE}..."
    cd "${ENGINE_DIR}"

    # Activate engine virtual environment
    if [ -f "${ENGINE_DIR}/.venv/bin/activate" ]; then
        source "${ENGINE_DIR}/.venv/bin/activate"
        log_info "Activated engine virtual environment"
    else
        log_error "Adapter virtual environment not found at ${ENGINE_DIR}/.venv"
        return 1
    fi

    # Export engine type and start
    export CHAT_ENGINE
    nohup "${run_script}" --port 20003 -l -e "${CHAT_ENGINE}" >> "${LOG_DIR}/engine.log" 2>&1 &
    local engine_pid=$!

    sleep 2

    # Verify process started successfully
    if kill -0 "$engine_pid" 2>/dev/null; then
        log_info "Adapter started successfully (PID: ${engine_pid})"
        log_info "Adapter is running on port 20003 with engine: ${CHAT_ENGINE}"
    else
        log_error "Failed to start engine. Check logs at ${LOG_DIR}/engine.log"
        return 1
    fi
}

engine_stop() {
    log_info "Stopping engine..."
    stop_port_processes_if_owned 20003 "${PROJECT_ROOT}" "engine" || true
    stop_matching_processes_if_owned "engine.community.api.app" "${PROJECT_ROOT}" "engine process" || true
    log_info "Adapter stopped"
}

engine_status() {
    local engine_pid=$(lsof -ti :20003 2>/dev/null | head -1)
    if [ -n "$engine_pid" ]; then
        echo "  Engine:    Running (PID: $engine_pid, port: 20003)"
    else
        echo "  Engine:    Stopped"
    fi
}

engine_prereqs() {
    local has_error=false

    echo -e "${CYAN}[engine] Prerequisites${NC}"

    if check_uv_installed; then
        prereq_ok "uv: $(uv --version 2>&1 | head -1)"
    else
        prereq_error "uv not found. Run: singlebox.sh install-tools"
        has_error=true
    fi

    if check_directory_exists "${ENGINE_DIR}" "engine" 2>/dev/null; then
        prereq_ok "directory: ${ENGINE_DIR}"
    else
        prereq_error "directory not found: ${ENGINE_DIR}"
        has_error=true
    fi

    # Virtual environment (created by engine_setup / uv sync)
    if [ -f "${ENGINE_DIR}/.venv/bin/activate" ]; then
        prereq_ok "venv: ${ENGINE_DIR}/.venv"
    else
        prereq_error "venv not found: ${ENGINE_DIR}/.venv — run: singlebox.sh setup engine"
        has_error=true
    fi

    if check_port_available 20003; then
        prereq_ok "Port 20003 available"
    else
        prereq_warn "Port 20003 is in use"
    fi

    if [ "$has_error" = true ]; then
        return 1
    fi
    return 0
}

engine_help() {
    echo "engine - OpenClawEnterprise engine adapter (port 20003)"
}

# Dispatch to the correct underlying engine based on CHAT_ENGINE
start_underlying_engine() {
    log_info "Starting underlying engine for: ${CHAT_ENGINE}"
    case "${CHAT_ENGINE}" in
        openclaw)
            if [ "$LOCAL_MODE" = true ]; then
                log_info "OpenClaw gateway will be launched on-demand per bot by the backend (local mode)"
            else
                openclaw_start
            fi
            ;;
        hermes)
            if [ "$LOCAL_MODE" = true ]; then
                log_info "Hermes dashboard will be launched on-demand per bot by the backend (local mode)"
            else
                hermes_start
            fi
            ;;
        aicoding)
            if [ "$LOCAL_MODE" = true ]; then
                log_info "Relay will be launched on-demand per bot by the backend (local mode)"
            else
                relay_start
            fi
            ;;
        *)
            log_warn "Unknown engine type: ${CHAT_ENGINE}, skipping underlying engine startup"
            ;;
    esac
}
