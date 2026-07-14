#!/usr/bin/env bash
# scripts/modules/baas.sh — BAAS (Bot as a Service) module
[[ -n "${_BAAS_SH_LOADED:-}" ]] && return 0
_BAAS_SH_LOADED=1

# Service-specific constants
BAAS_LOG="${LOG_DIR}/baas.log"
BAAS_APP_DIR="${BAAS_APP_DIR:-${BAAS_DIR}/packages/community}"

baas_normalize_dir_path() {
    local path="$1"

    while [[ "$path" != "/" && "$path" == */ ]]; do
        path="${path%/}"
    done
    printf '%s\n' "$path"
}

baas_backup_bcs_sessions() {
    local backup_dir="$1"
    local session_file relative_path destination manifest
    local bots_dir backup_dir_norm
    bots_dir="$(baas_normalize_dir_path "${LOCAL_BOTS_DIR}")"
    backup_dir_norm="$(baas_normalize_dir_path "$backup_dir")"

    if [[ -e "$backup_dir_norm" ]]; then
        log_error "Refusing to overwrite existing BCS session backup: ${backup_dir_norm}"
        return 1
    fi
    [[ -d "$bots_dir" ]] || return 0

    manifest="$(mktemp "${TMPDIR:-/tmp}/avernet-bcs-sessions.XXXXXX")" || return 1
    if ! find "$bots_dir" -type f -path '*/.bcs/session.json' -print0 > "$manifest"; then
        rm -f "$manifest"
        return 1
    fi

    while IFS= read -r -d '' session_file; do
        relative_path="${session_file#"${bots_dir}/"}"
        destination="${backup_dir_norm}/${relative_path}"
        (umask 077 && mkdir -p "$(dirname "$destination")") || {
            rm -f "$manifest"
            return 1
        }
        cp -p "$session_file" "$destination" || {
            rm -f "$manifest"
            return 1
        }
        chmod 600 "$destination" || {
            rm -f "$manifest"
            return 1
        }
    done < "$manifest"
    rm -f "$manifest"
}

baas_restore_bcs_sessions() {
    local backup_dir="$1"
    local session_file relative_path destination manifest
    local bots_dir backup_dir_norm
    bots_dir="$(baas_normalize_dir_path "${LOCAL_BOTS_DIR}")"
    backup_dir_norm="$(baas_normalize_dir_path "$backup_dir")"

    [[ -d "$backup_dir_norm" ]] || return 0

    manifest="$(mktemp "${TMPDIR:-/tmp}/avernet-bcs-sessions.XXXXXX")" || return 1
    if ! find "$backup_dir_norm" -type f -path '*/.bcs/session.json' -print0 > "$manifest"; then
        rm -f "$manifest"
        return 1
    fi

    while IFS= read -r -d '' session_file; do
        relative_path="${session_file#"${backup_dir_norm}/"}"
        destination="${bots_dir}/${relative_path}"
        if [[ -e "$destination" ]]; then
            continue
        fi
        (umask 077 && mkdir -p "$(dirname "$destination")") || {
            rm -f "$manifest"
            return 1
        }
        cp -p "$session_file" "$destination" || {
            rm -f "$manifest"
            return 1
        }
        chmod 600 "$destination" || {
            rm -f "$manifest"
            return 1
        }
    done < "$manifest"

    rm -f "$manifest"
    rm -rf "$backup_dir_norm"
}

baas_prepare_bcs_session_backup() {
    local backup_dir="$1"

    if [[ -d "$backup_dir" ]]; then
        log_warn "Recovering stale BCS session backup before cleanup: ${backup_dir}"
        baas_restore_bcs_sessions "$backup_dir" || return 1
    fi
    baas_backup_bcs_sessions "$backup_dir"
}

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
    require_port_available_after_owned_stop 8890 "BAAS" "stop the service using port 8890" || return 1

    log_info "Starting BAAS service (singlebox mode)..."

    if ! check_directory_exists "${BAAS_APP_DIR}" "baas"; then
        return 1
    fi

    cd "${BAAS_APP_DIR}"

    # Start baas (singlebox mode)
    # app.sh internally includes health check, no external retry needed
    log_info "Starting BAAS with singlebox mode..."

    if [ "$LOCAL_MODE" = true ]; then
      local bcs_session_backup="${RUNTIME_DATA_DIR}/.baas-bcs-sessions"
      local bots_dir
      bots_dir="$(baas_normalize_dir_path "${LOCAL_BOTS_DIR}")"

      if [[ -z "$bots_dir" || "$bots_dir" == "/" ]]; then
          log_error "Refusing to clean empty or root LOCAL_BOTS_DIR"
          return 1
      fi

      # Remove existing SQLite database to start fresh
      if [ -f "${RUNTIME_DATA_DIR}/baas.db" ]; then
          log_info "Removing existing SQLite database for fresh start..."
          rm -f "${RUNTIME_DATA_DIR}/baas.db"
      fi

      # Clean bots dir
      if [[ -d "$bots_dir" ]]; then
          log_info "Cleaning bots dir: ${bots_dir} ..."
          baas_prepare_bcs_session_backup "$bcs_session_backup" || {
              log_error "Failed to back up BCS sessions before cleaning bots dir"
              return 1
          }
          rm -rf "${bots_dir:?}/"*
          baas_restore_bcs_sessions "$bcs_session_backup" || {
              log_error "Failed to restore BCS sessions after cleaning bots dir"
              return 1
          }
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

    local pid_file="${BAAS_APP_DIR}/tmp/app.pid"
    local port_file="${BAAS_APP_DIR}/tmp/app.port"
    local pid=""
    local port="8890"

    if [ -f "$pid_file" ]; then
        pid="$(cat "$pid_file" 2>/dev/null || true)"
    fi
    if [ -f "$port_file" ]; then
        port="$(cat "$port_file" 2>/dev/null || echo "8890")"
    fi

    if [ -n "$pid" ]; then
        stop_process_if_owned "$pid" "${PROJECT_ROOT}" "BAAS pidfile process" || true
    fi

    stop_matching_processes_if_owned "secbaas/main.py" "${PROJECT_ROOT}" "BAAS process" || true
    stop_port_processes_if_owned "$port" "${PROJECT_ROOT}" "BAAS" || true
    rm -f "$pid_file" "$port_file"

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
