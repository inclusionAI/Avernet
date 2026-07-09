#!/usr/bin/env bash
# scripts/modules/backend.sh — Backend (agentclaw) service module
[[ -n "${_BACKEND_SH_LOADED:-}" ]] && return 0
_BACKEND_SH_LOADED=1

# 服务专属常量
BACKEND_LOG="${LOG_DIR}/backend.log"
BACKEND_LOCAL_HOST="${BACKEND_LOCAL_HOST:-agentclaw-local.localhost}"
BACKEND_DEVICE_LOCAL_HOST="${BACKEND_DEVICE_LOCAL_HOST:-agentclawdevice-local.localhost}"

backend_setup() {
    if ! check_directory_exists "${BACKEND_DIR}" "backend"; then
        return 1
    fi

    check_uv_installed || { log_error "uv not found. Run: $0 setup"; return 1; }

    cd "${BACKEND_DIR}"
    log_info "Syncing Python dependencies with uv..."
    if ! uv sync --index-url "${PYPI_INDEX_URL}"; then
        log_error "Failed to sync Python dependencies"
        return 1
    fi
    log_info "Python dependencies synced successfully"
}

backend_start() {
    mkdir -p "${LOG_DIR}"

    stop_port_processes_if_owned 8888 "${PROJECT_ROOT}" "existing backend"
    stop_matching_processes_if_owned "agentclaw/community/main.py" "${PROJECT_ROOT}" "existing backend process"

    log_info "Starting backend service..."

    if ! check_directory_exists "${BACKEND_DIR}" "backend"; then
        return 1
    fi

    cd "${BACKEND_DIR}"

    # 激活虚拟环境
    local current_python=$(which python 2>/dev/null || echo "")
    local venv_python="${BACKEND_DIR}/.venv/bin/python"

    if [ "$current_python" != "${venv_python}" ]; then
        if [ -f "${BACKEND_DIR}/.venv/bin/activate" ]; then
            source "${BACKEND_DIR}/.venv/bin/activate"
            log_info "Virtual environment activated"
        else
            log_error "Virtual environment not found at ${BACKEND_DIR}/.venv"
            return 1
        fi
    else
        log_info "Already in virtual environment"
    fi

    # B11 extraction: agentclaw.community now lives in the openocb submodule; run its
    # main.py from there and put its src on PYTHONPATH (namespace-aggregates with the
    # editable corp src). The process greps below match on the "agentclaw/community/main.py"
    # substring, which the fuller submodule path still contains.
    local community_src="${BACKEND_DIR}/src"
    local community_main="${community_src}/agentclaw/community/main.py"
    local backend_cmd=(
        python
        "${community_main}"
    )
    if [ "${SINGLEBOX_COVERAGE:-0}" = "1" ]; then
        local coverage_dir="${SINGLEBOX_COVERAGE_DIR:-${DEP_DIR}/coverage/singlebox/raw}/backend"
        mkdir -p "$coverage_dir"
        export COVERAGE_FILE="${coverage_dir}/.coverage"
        uv pip install --python "$venv_python" coverage >/dev/null
        backend_cmd=(
            "$venv_python" -m coverage run
            --parallel-mode
            --save-signal=USR1
            --source="${community_src}"
            "${community_main}"
        )
        log_info "Backend coverage mode enabled: ${coverage_dir}"
    fi

    log_info "Engine type: ${CHAT_ENGINE}"
    local backend_pid
    if [ "$LOCAL_MODE" = true ]; then
        # Remove existing SQLite database to start fresh
        if [ -f "${RUNTIME_DATA_DIR}/backend.db" ]; then
            log_info "Removing existing SQLite database for fresh start..."
            rm -f "${RUNTIME_DATA_DIR}/backend.db"
        fi

        SERVER_ENV=singlebox DEPLOY_PROFILE=singlebox \
            DATABASE_URL="sqlite:///${RUNTIME_DATA_DIR}/backend.db" \
            ENABLE_OSS_SYNC=false \
            CHAT_ENGINE="${CHAT_ENGINE}" \
            AIDESKTOP_ROOT="${LOCAL_AIDESKTOP_DIR}" \
            LOCAL_AIDESKTOP_ROOT="${LOCAL_AIDESKTOP_DIR}" \
            PYTHONPATH="${community_src}:${BACKEND_DIR}:${PYTHONPATH:-}" \
            nohup "${backend_cmd[@]}" < /dev/null >> "${BACKEND_LOG}" 2>&1 &
        backend_pid=$!
    else
        SERVER_ENV=singlebox DEPLOY_PROFILE=corp CHAT_ENGINE="${CHAT_ENGINE}" \
            AIDESKTOP_ROOT="${LOCAL_AIDESKTOP_DIR}" \
            LOCAL_AIDESKTOP_ROOT="${LOCAL_AIDESKTOP_DIR}" \
            PYTHONPATH="${community_src}:${BACKEND_DIR}:${PYTHONPATH:-}" \
            nohup "${backend_cmd[@]}" < /dev/null >> "${BACKEND_LOG}" 2>&1 &
        backend_pid=$!
    fi

    backend_wait_until_ready "$backend_pid" || return 1

    # 验证进程是否启动成功
    if kill -0 "$backend_pid" 2>/dev/null; then
        log_info "Backend started successfully (PID: $backend_pid)"
    else
        log_error "Failed to start backend. Check logs at ${BACKEND_LOG}"
        return 1
    fi
}

backend_wait_until_ready() {
    local pid="${1:-}"
    local attempt=0
    local max_attempts="${BACKEND_READY_ATTEMPTS:-20}"
    while [ "$attempt" -lt "$max_attempts" ]; do
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
            log_error "Backend process exited before becoming ready. Last backend log lines:"
            tail -n 40 "${BACKEND_LOG}" 2>/dev/null | while IFS= read -r line; do
                log_error "  ${line}"
            done
            backend_stop
            return 1
        fi
        if backend_ready; then
            return 0
        fi
        sleep 0.5
        attempt=$((attempt + 1))
    done

    log_error "Backend did not become ready. Last backend log lines:"
    tail -n 40 "${BACKEND_LOG}" 2>/dev/null | while IFS= read -r line; do
        log_error "  ${line}"
    done
    backend_stop
    return 1
}

backend_stop() {
    log_info "Stopping backend..."
    stop_matching_processes_if_owned "agentclaw/community/main.py" "${PROJECT_ROOT}" "backend process" || true
    stop_port_processes_if_owned 8888 "${PROJECT_ROOT}" "backend" || true
    log_info "Backend stopped"
}

backend_status() {
    local backend_pid=$(ps aux | grep -v grep | grep "agentclaw/community/main.py" | awk '{print $2}' | head -1)
    if [ -n "$backend_pid" ]; then
        echo "  Backend:   Running (PID: $backend_pid)"
    else
        echo "  Backend:   Stopped"
    fi
}

backend_ready() {
    curl --noproxy '*' --connect-timeout 1 --max-time 2 -s "http://127.0.0.1:8888/api/health" > /dev/null 2>&1
}

backend_host_needs_hosts_entry() {
    case "$1" in
        *.localhost|localhost|127.0.0.1)
            return 1
            ;;
        *)
            return 0
            ;;
    esac
}

backend_prereqs() {
    local has_error=false

    echo -e "${CYAN}[backend] Prerequisites${NC}"

    # Tool: uv (manages its own Python — venv version is determined by pyproject.toml)
    if check_uv_installed; then
        prereq_ok "uv: $(uv --version 2>&1 | head -1)"
    else
        prereq_error "uv not found. Run: singlebox.sh install-tools"
        has_error=true
    fi

    # Hosts: local backend + device endpoints
    local missing_hosts=()
    if backend_host_needs_hosts_entry "${BACKEND_LOCAL_HOST}" && ! grep -q "127.0.0.1.*${BACKEND_LOCAL_HOST}" /etc/hosts 2>/dev/null; then
        missing_hosts+=("${BACKEND_LOCAL_HOST}")
    fi
    if backend_host_needs_hosts_entry "${BACKEND_DEVICE_LOCAL_HOST}" && ! grep -q "127.0.0.1.*${BACKEND_DEVICE_LOCAL_HOST}" /etc/hosts 2>/dev/null; then
        missing_hosts+=("${BACKEND_DEVICE_LOCAL_HOST}")
    fi
    if [ ${#missing_hosts[@]} -eq 0 ]; then
        prereq_ok "hosts: ${BACKEND_LOCAL_HOST} + ${BACKEND_DEVICE_LOCAL_HOST} resolve locally"
    else
        for host in "${missing_hosts[@]}"; do
            prereq_error "hosts: $host not bound to 127.0.0.1 in /etc/hosts"
        done
        has_error=true
    fi

    # Directory: BACKEND_DIR
    if [ -d "${BACKEND_DIR}" ]; then
        prereq_ok "directory: ${BACKEND_DIR}"
    else
        prereq_error "directory not found: ${BACKEND_DIR}"
        has_error=true
    fi

    # Virtual environment (created by backend_setup / uv sync)
    if [ -f "${BACKEND_DIR}/.venv/bin/activate" ]; then
        prereq_ok "venv: ${BACKEND_DIR}/.venv"
    else
        prereq_error "venv not found: ${BACKEND_DIR}/.venv — run: singlebox.sh setup backend"
        has_error=true
    fi

    # Ports: 8888
    for port in 8888; do
        if check_port_available "$port"; then
            prereq_ok "Port $port available"
        else
            prereq_warn "Port $port is in use"
        fi
    done

    if [ "$has_error" = true ]; then
        return 1
    fi
    return 0
}

backend_help() {
    echo "backend - Backend service (port 8888)"
}
