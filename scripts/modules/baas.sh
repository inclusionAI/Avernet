#!/usr/bin/env bash
# scripts/modules/baas.sh — BAAS (Bot as a Service) module
[[ -n "${_BAAS_SH_LOADED:-}" ]] && return 0
_BAAS_SH_LOADED=1

# Service-specific constants
BAAS_LOG="${LOG_DIR}/baas.log"
BAAS_APP_DIR="${BAAS_APP_DIR:-${BAAS_DIR}/packages/community}"

baas_setup() {
    # Setup the underlying engine that baas spawns on demand
    local engine_svc=$(_resolve_engine_svc)
    if type -t "${engine_svc}_setup" &>/dev/null; then
        log_info ">>> Setting up underlying engine: ${engine_svc}..."
        "${engine_svc}_setup" || log_warn "${engine_svc} setup failed, continuing..."
    fi

    # Setup the engine adapter that baas spawns on demand
    if type -t "engine_setup" &>/dev/null; then
        engine_setup || log_warn "engine setup failed, continuing..."
    fi

    if ! check_directory_exists "${BAAS_APP_DIR}" "baas"; then
        return 1
    fi

    cd "${BAAS_APP_DIR}"

    # Check uv is installed
    check_uv_installed || { log_error "uv not found. Run: $0 setup"; return 1; }

    # Sync Python dependencies
    log_info "Syncing Python dependencies for BAAS..."
    if ! uv sync --index-url "${PYPI_INDEX_URL}"; then
        log_error "Failed to sync Python dependencies for BAAS"
        return 1
    fi
    log_info "BAAS dependencies synced successfully"
}

baas_start() {
    mkdir -p "${LOG_DIR}"

    stop_port_processes_if_owned 8890 "${PROJECT_ROOT}" "existing BAAS"
    stop_matching_processes_if_owned "secbaas/main.py" "${PROJECT_ROOT}" "existing BAAS process"

    log_info "Starting BAAS service (singlebox mode)..."

    if ! check_directory_exists "${BAAS_APP_DIR}" "baas"; then
        return 1
    fi

    cd "${BAAS_APP_DIR}"

    # Start baas (singlebox mode)
    # app.sh internally includes health check, no external retry needed
    log_info "Starting BAAS with singlebox mode..."

    if [ "$LOCAL_MODE" = true ]; then
      # Remove existing SQLite database to start fresh
      if [ -f "${RUNTIME_DATA_DIR}/baas.db" ]; then
          log_info "Removing existing SQLite database for fresh start..."
          rm -f "${RUNTIME_DATA_DIR}/baas.db"
      fi

      # Clean bots dir
      if [[ -d "${LOCAL_BOTS_DIR}" ]]; then
          log_info "Cleaning bots dir: ${LOCAL_BOTS_DIR} ..."
          rm -rf "${LOCAL_BOTS_DIR:?}/"*
      fi
    fi

    # Build env vars to pass to baas
    local baas_env_args=()
    mkdir -p "${LOCAL_AIDESKTOP_DIR}"
    DATABASE_URL="sqlite:///${RUNTIME_DATA_DIR}/baas.db"
    baas_env_args+=(
        DATABASE_URL="${DATABASE_URL}"
        CHAT_ENGINE="${CHAT_ENGINE}"
        LOCAL_AIDESKTOP_ROOT="${LOCAL_AIDESKTOP_DIR}"
        SINGLEBOX_MODEL_CONFIG_FILE="${SINGLEBOX_MODEL_CONFIG_FILE:-}"
    )
    log_info "BAAS env: DATABASE_URL=${DATABASE_URL}, CHAT_ENGINE=${CHAT_ENGINE}, LOCAL_AIDESKTOP_ROOT=${LOCAL_AIDESKTOP_DIR}"

    if ! env "${baas_env_args[@]}" "${BAAS_APP_DIR}/scripts/app.sh" start --singlebox >> "${BAAS_LOG}" 2>&1; then
        log_error "Failed to start BAAS. Check logs at ${BAAS_LOG}"
        return 1
    fi

    log_info "BAAS started successfully"
    log_info "BAAS health check: http://localhost:8890/health"
}

baas_stop() {
    log_info "Stopping BAAS service..."

    cd "${BAAS_APP_DIR}"

    # Use baas script to stop (includes graceful shutdown + port cleanup internally)
    if [ -f "${BAAS_APP_DIR}/scripts/app.sh" ]; then
        "${BAAS_APP_DIR}/scripts/app.sh" stop 2>/dev/null || true
    fi

    log_info "BAAS service stopped"
}

baas_status() {
    if [ -f "${BAAS_APP_DIR}/scripts/app.sh" ]; then
        # cd to baas dir to ensure PORT_FILE can be read
        cd "${BAAS_APP_DIR}" 2>/dev/null || true
        local baas_status=$("${BAAS_APP_DIR}/scripts/app.sh" status 2>/dev/null || echo "Stopped")
        # Strip color codes
        echo "  BAAS:      ${baas_status}" | sed 's/\x1b\[[0-9;]*m//g'
    else
        echo "  BAAS:      Not installed"
    fi
}

baas_prereqs() {
    local has_error=false

    # Check the underlying engine that baas spawns on demand
    local engine_svc=$(_resolve_engine_svc)
    if [ -n "$engine_svc" ] && type -t "${engine_svc}_prereqs" &>/dev/null; then
        "${engine_svc}_prereqs" || has_error=true
    fi

    # Check the engine adapter that baas spawns on demand
    if type -t "engine_prereqs" &>/dev/null; then
        engine_prereqs || has_error=true
    fi

    echo -e "${CYAN}[baas] Prerequisites${NC}"

    if check_uv_installed; then
        prereq_ok "uv: $(uv --version 2>&1 | head -1)"
    else
        prereq_error "uv not found. Run: singlebox.sh install-tools"
        has_error=true
    fi

    if [ -d "${BAAS_APP_DIR}" ]; then
        prereq_ok "directory: ${BAAS_APP_DIR}"
    else
        prereq_error "directory not found: ${BAAS_APP_DIR}"
        has_error=true
    fi

    # Virtual environment (created by baas_setup / uv sync)
    # app.sh auto-creates venv if missing, so this is a warning, not an error.
    if [ -f "${BAAS_APP_DIR}/.venv/bin/activate" ]; then
        prereq_ok "venv: ${BAAS_APP_DIR}/.venv"
    else
        prereq_warn "venv not found: ${BAAS_APP_DIR}/.venv — will be created on first start (slow)"
    fi

    if check_port_available 8890; then
        prereq_ok "Port 8890 available"
    else
        prereq_warn "Port 8890 is in use"
    fi

    if [ "$has_error" = true ]; then
        return 1
    fi
    return 0
}

baas_help() {
    echo "baas - BAAS (Bot as a Service) (port 8890)"
}
