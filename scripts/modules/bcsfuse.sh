#!/usr/bin/env bash
# scripts/modules/bcsfuse.sh — BCSFuse context fusion & semantic search module
[[ -n "${_BCSFUSE_SH_LOADED:-}" ]] && return 0
_BCSFUSE_SH_LOADED=1

# Service-specific constants (BCSFUSE_RUNTIME_DIR is overridden in standalone
# mode by apply_singlebox_mode_defaults in singlebox.sh)
BCSFUSE_LOG="${LOG_DIR}/bcsfuse.log"
# BCSFUSE_RUNTIME_DIR default is resolved lazily in bcsfuse_load_env()
# so that standalone mode can override it before any function runs.
BCSFUSE_RUNTIME_DIR="${BCSFUSE_RUNTIME_DIR:-}"  # set in bcsfuse_load_env()
BCSFUSE_ENV_FILE=""
BCSFUSE_PID_FILE=""

# ============ Environment ============

bcsfuse_load_env() {
    # Resolve BCSFUSE_RUNTIME_DIR lazily so standalone mode can override it
    # via apply_singlebox_mode_defaults (called after source but before any
    # bcsfuse_* function runs).
    BCSFUSE_RUNTIME_DIR="${BCSFUSE_RUNTIME_DIR:-${BCSFUSE_DIR}/.runtime}"
    BCSFUSE_ENV_FILE="${BCSFUSE_RUNTIME_DIR}/env/.env.local"
    BCSFUSE_PID_FILE="${BCSFUSE_RUNTIME_DIR}/pids/open_core.pid"

    # Layer 1: Project-level .env.local (shared with other modules)
    load_repo_env_file "${PROJECT_ROOT}/.env.local"

    # Ensure provider mode defaults to dev
    export BCSFUSE_PROVIDER_MODE="${BCSFUSE_PROVIDER_MODE:-dev}"
    export BCSFUSE_SERVER_PORT="${BCSFUSE_PORT:-8765}"

    # Compat mapping: reuse OPENCLAW_OPENAI_* if bcsfuse vars not set
    # This lets users configure one set of API keys for both bots and bcsfuse
    [ -z "${LLM_BASE_URL:-}" ] && [ -n "${OPENCLAW_OPENAI_BASE_URL:-}" ] && export LLM_BASE_URL="${OPENCLAW_OPENAI_BASE_URL}" || true
    [ -z "${LLM_AUTH_TOKEN:-}" ] && [ -n "${OPENCLAW_OPENAI_API_KEY:-}" ] && export LLM_AUTH_TOKEN="${OPENCLAW_OPENAI_API_KEY}" || true
    [ -z "${EMBEDDING_BASE_URL:-}" ] && [ -n "${OPENCLAW_OPENAI_BASE_URL:-}" ] && export EMBEDDING_BASE_URL="${OPENCLAW_OPENAI_BASE_URL}" || true
    [ -z "${EMBEDDING_AUTH_TOKEN:-}" ] && [ -n "${OPENCLAW_OPENAI_API_KEY:-}" ] && export EMBEDDING_AUTH_TOKEN="${OPENCLAW_OPENAI_API_KEY}" || true
}

# ============ MySQL (runtime mode only) ============

bcsfuse_check_mysql() {
    local host="${MYSQL_HOST:-127.0.0.1}"
    local port="${MYSQL_PORT:-3306}"
    local user="${MYSQL_USER:-root}"
    local pass="${MYSQL_PASSWORD:-}"
    local db="${MYSQL_DATABASE:-bcsfuse_oss}"

    # Try mysql client first
    if command -v mysql >/dev/null 2>&1; then
        if [ -n "$pass" ]; then
            mysql -h"$host" -P"$port" -u"$user" -p"$pass" -e "USE \`$db\`" >/dev/null 2>&1
        else
            mysql -h"$host" -P"$port" -u"$user" -e "USE \`$db\`" >/dev/null 2>&1
        fi
        return $?
    fi

    # Fallback: Python check
    python3 -c "
import mysql.connector, os
conn = mysql.connector.connect(
    host='$host', port=$port,
    user='$user', password='${pass}',
    database='$db', connect_timeout=3
)
conn.close()
" >/dev/null 2>&1
    return $?
}

bcsfuse_ensure_schema() {
    # Idempotent: only runs when MySQL is reachable (called after bcsfuse_check_mysql)
    local schema_script="${BCSFUSE_DIR}/tests/smoke/runtime_mysql_schema_setup.py"
    if [ -f "$schema_script" ]; then
        log_info "bcsfuse: Ensuring MySQL schema is up to date..."
        cd "${BCSFUSE_DIR}"
        if ! python3 "$schema_script" setup; then
            log_error "bcsfuse: MySQL schema init failed"
            return 1
        fi
        log_info "bcsfuse: MySQL schema ready"
    else
        log_warn "bcsfuse: Schema script not found: ${schema_script}"
    fi
    return 0
}

# ============ LLM / Embedding checks ============

bcsfuse_check_llm() {
    # Returns 0 if LLM is configured, 1 if not (warn-level, never fatal)
    if [ -z "${LLM_BASE_URL:-}" ] || [ "${LLM_BASE_URL}" = "change_me" ]; then
        return 1
    fi
    return 0
}

bcsfuse_check_embedding() {
    if [ -z "${EMBEDDING_BASE_URL:-}" ] || [ "${EMBEDDING_BASE_URL}" = "change_me" ]; then
        return 1
    fi
    return 0
}

# ============ Setup ============

bcsfuse_setup() {
    log_info "Setting up bcsfuse..."

    if ! check_directory_exists "${BCSFUSE_DIR}" "bcsfuse"; then
        return 1
    fi

    cd "${BCSFUSE_DIR}"

    # Python dependencies
    check_uv_installed || { log_error "uv not found. Run: singlebox.sh install-tools"; return 1; }

    log_info "Syncing Python dependencies for bcsfuse..."
    if ! uv sync --index-url "${PYPI_INDEX_URL}"; then
        log_error "Failed to sync Python dependencies for bcsfuse"
        return 1
    fi
    log_info "bcsfuse dependencies synced successfully"

    # Create .runtime/ directory structure
    mkdir -p "${BCSFUSE_RUNTIME_DIR}"/{logs,pids,data,env}

    # Generate env file from template if it doesn't exist
    if [ ! -f "${BCSFUSE_ENV_FILE}" ] && [ -f "${BCSFUSE_DIR}/.env.example" ]; then
        cp "${BCSFUSE_DIR}/.env.example" "${BCSFUSE_ENV_FILE}"
        log_warn "bcsfuse: Generated env from .env.example — edit LLM/Embedding config:"
        log_warn "  vi ${BCSFUSE_ENV_FILE}"
    fi

    # .runtime/.gitignore
    local gitignore_file="${BCSFUSE_RUNTIME_DIR}/.gitignore"
    if [ ! -f "$gitignore_file" ]; then
        cat > "$gitignore_file" << 'EOF'
# BCSFuse Runtime Directory — DO NOT commit contents
logs/
pids/
data/
env/
*
!.gitignore
EOF
    fi

    # Runtime mode: init MySQL schema if reachable
    if [ "${BCSFUSE_PROVIDER_MODE:-dev}" = "runtime" ]; then
        if bcsfuse_check_mysql; then
            bcsfuse_ensure_schema
        else
            log_warn "bcsfuse: MySQL not reachable — schema init skipped (will be checked at start)"
        fi
    fi
}

# ============ Start ============

bcsfuse_start() {
    bcsfuse_load_env

    local mode="${BCSFUSE_PROVIDER_MODE:-dev}"
    mkdir -p "${LOG_DIR}"

    # Kill old processes — use broad port kill + precise path match to avoid
    # clobbering unrelated Python processes (e.g. other `main.py` instances).
    kill_port_process "${BCSFUSE_PORT}"
    pkill -f "python.*${BCSFUSE_DIR}/main.py" 2>/dev/null || true

    # Runtime mode: MySQL must be reachable
    if [ "$mode" = "runtime" ]; then
        if ! bcsfuse_check_mysql; then
            log_error "bcsfuse: runtime mode requires MySQL but it's not available"
            log_error "  Ensure MySQL is running and MYSQL_HOST/PORT/USER/PASSWORD are correct"
            log_error "  Or use dev mode: BCSFUSE_PROVIDER_MODE=dev"
            return 1
        fi
        bcsfuse_ensure_schema
    fi

    # LLM/Embedding: warn if not configured (not fatal, consistent with bots module)
    if ! bcsfuse_check_llm; then
        log_warn "bcsfuse: LLM_BASE_URL not set — search/recommend/fusion will be limited"
    fi
    if ! bcsfuse_check_embedding; then
        log_warn "bcsfuse: EMBEDDING_BASE_URL not set — semantic search will return empty results"
    fi

    # Ensure runtime dirs exist
    mkdir -p "${BCSFUSE_RUNTIME_DIR}"/{logs,pids,data}

    # Point dev-mode SQLite/Faiss data paths into .runtime/data/ so all
    # runtime state lives under one directory tree (easy to clean/reset).
    # When these env vars are set, resolve_data_path() and config defaults
    # are overridden.
    export BCSFUSE_DATABASE_SQLITE_PATH="${BCSFUSE_RUNTIME_DIR}/data/bcsfuse.db"
    export BCSFUSE_FAISS_SQLITE_PATH="${BCSFUSE_RUNTIME_DIR}/data/faiss_index.db"
    export QDRANT_LOCAL_PATH="${BCSFUSE_RUNTIME_DIR}/data/qdrant"

    # Clear old log
    : > "${BCSFUSE_LOG}"

    # Start runtime
    cd "${BCSFUSE_DIR}"
    log_info "Starting bcsfuse (${mode}) on port ${BCSFUSE_PORT}..."
    nohup uv run python main.py >> "${BCSFUSE_LOG}" 2>&1 &
    local pid=$!
    echo "$pid" > "${BCSFUSE_PID_FILE}"

    # Wait for startup
    sleep 3

    # Verify process is alive
    if ! ps -p "$pid" >/dev/null 2>&1; then
        log_error "bcsfuse: Process exited early (PID: $pid)"
        log_error "Check log: ${BCSFUSE_LOG}"
        tail -40 "${BCSFUSE_LOG}" >&2 || true
        return 1
    fi

    # Wait for health check (up to 30 seconds)
    local health_url="http://localhost:${BCSFUSE_PORT}/health"
    local retries=15
    local passed=false

    while [ $retries -gt 0 ]; do
        if curl -sf --max-time 2 "$health_url" 2>/dev/null | grep -qi "healthy\|ok\|pass\|200"; then
            passed=true
            break
        fi
        sleep 2
        retries=$((retries - 1))
    done

    if [ "$passed" = true ]; then
        log_info "bcsfuse started successfully on port ${BCSFUSE_PORT} (${mode} mode)"
    else
        log_error "bcsfuse: Health check failed after startup"
        log_error "Check log: ${BCSFUSE_LOG}"
        tail -40 "${BCSFUSE_LOG}" >&2 || true
        return 1
    fi
}

# ============ Stop ============

bcsfuse_stop() {
    log_info "Stopping bcsfuse..."

    # Graceful shutdown via PID file
    if [ -f "${BCSFUSE_PID_FILE}" ]; then
        local pid
        pid=$(cat "${BCSFUSE_PID_FILE}" 2>/dev/null || echo "")
        if [ -n "$pid" ] && ps -p "$pid" >/dev/null 2>&1; then
            log_info "Sending SIGTERM to bcsfuse (PID: $pid)..."
            kill -TERM "$pid" 2>/dev/null || true

            # Wait up to 10 seconds for graceful shutdown
            local waited=0
            while [ $waited -lt 10 ]; do
                if ! ps -p "$pid" >/dev/null 2>&1; then
                    log_info "bcsfuse stopped gracefully"
                    break
                fi
                sleep 1
                waited=$((waited + 1))
            done

            # Force kill if still alive
            if ps -p "$pid" >/dev/null 2>&1; then
                log_warn "bcsfuse: Graceful shutdown timeout, force killing..."
                kill -KILL "$pid" 2>/dev/null || true
            fi
        fi
        rm -f "${BCSFUSE_PID_FILE}"
    fi

    # Fallback: kill by port and precise process path
    kill_port_process "${BCSFUSE_PORT}"
    pkill -f "python.*${BCSFUSE_DIR}/main.py" 2>/dev/null || true

    log_info "bcsfuse stopped"
}

# ============ Restart ============

bcsfuse_restart() {
    bcsfuse_stop
    sleep 2
    bcsfuse_start
}

# ============ Clean ============

bcsfuse_clean() {
    log_info "Cleaning bcsfuse runtime data..."

    # Stop first
    bcsfuse_stop || true

    # Clean .runtime/data (Qdrant/Faiss data used by runtime mode)
    local runtime_data_dir="${BCSFUSE_RUNTIME_DIR}/data"
    if [ -d "$runtime_data_dir" ]; then
        log_info "Removing bcsfuse runtime vector data: ${runtime_data_dir}"
        rm -rf "${runtime_data_dir:?}"/*
    fi

    # Clean project data/ directory (SQLite DBs, Faiss index — used by dev mode)
    local project_data_dir="${BCSFUSE_DIR}/data"
    if [ -d "$project_data_dir" ]; then
        log_info "Removing bcsfuse project data: ${project_data_dir}"
        rm -rf "${project_data_dir:?}"/*
    fi

    # Clean logs
    local logs_dir="${BCSFUSE_RUNTIME_DIR}/logs"
    if [ -d "$logs_dir" ]; then
        rm -rf "${logs_dir:?}"/*
    fi

    # Clean PIDs
    local pids_dir="${BCSFUSE_RUNTIME_DIR}/pids"
    if [ -d "$pids_dir" ]; then
        rm -rf "${pids_dir:?}"/*
    fi

    # Note: MySQL tables and .runtime/env/ are NOT cleaned
    log_info "bcsfuse cleaned (env and MySQL preserved)"
}

# ============ Status ============

bcsfuse_status() {
    local pid=""
    if [ -f "${BCSFUSE_PID_FILE}" ]; then
        pid=$(cat "${BCSFUSE_PID_FILE}" 2>/dev/null || echo "")
    fi

    # Also check port
    local port_pid
    port_pid=$(lsof -ti :"${BCSFUSE_PORT}" 2>/dev/null | head -1 || echo "")

    if [ -n "$pid" ] && ps -p "$pid" >/dev/null 2>&1; then
        # Health check
        local health="FAIL"
        if curl -sf --max-time 2 "http://localhost:${BCSFUSE_PORT}/health" 2>/dev/null | grep -qi "healthy\|ok\|pass\|200"; then
            health="PASS"
        fi
        echo "  BCSFuse:  Running (PID: $pid, port: ${BCSFUSE_PORT}, mode: ${BCSFUSE_PROVIDER_MODE:-dev}, health: ${health})"
    elif [ -n "$port_pid" ]; then
        echo "  BCSFuse:  Running (PID: $port_pid, port: ${BCSFUSE_PORT}, mode: ${BCSFUSE_PROVIDER_MODE:-dev}, pid_file: stale)"
    else
        echo "  BCSFuse:  Stopped"
    fi
}

# ============ Prerequisites ============

bcsfuse_prereqs() {
    bcsfuse_load_env

    local has_error=false
    local mode="${BCSFUSE_PROVIDER_MODE:-dev}"

    echo -e "${CYAN}[bcsfuse] Prerequisites${NC}"

    # uv
    if check_uv_installed; then
        prereq_ok "uv: $(uv --version 2>&1 | head -1)"
    else
        prereq_error "uv not found. Run: singlebox.sh install-tools"
        has_error=true
    fi

    # Directory
    if check_directory_exists "${BCSFUSE_DIR}" "bcsfuse" 2>/dev/null; then
        prereq_ok "directory: ${BCSFUSE_DIR}"
    else
        prereq_error "directory not found: ${BCSFUSE_DIR}"
        has_error=true
    fi

    # Virtual environment (created by bcsfuse_setup / uv sync)
    if [ -f "${BCSFUSE_DIR}/.venv/bin/activate" ]; then
        prereq_ok "venv: ${BCSFUSE_DIR}/.venv"
    else
        prereq_warn "venv not found: ${BCSFUSE_DIR}/.venv — run: singlebox.sh setup bcsfuse"
    fi

    # Port
    if check_port_available "${BCSFUSE_PORT}"; then
        prereq_ok "Port ${BCSFUSE_PORT} available"
    else
        prereq_warn "Port ${BCSFUSE_PORT} is in use"
    fi

    # BCS dependency — bcsfuse works alongside BCS
    if bcs_health_ready 2>/dev/null; then
        prereq_ok "BCS: running on port ${BCS_PORT}"
    else
        prereq_warn "BCS not running on port ${BCS_PORT} — bcsfuse may have limited functionality"
    fi

    # Mode
    prereq_ok "mode: ${mode}"

    # MySQL (runtime only)
    if [ "$mode" = "runtime" ]; then
        if bcsfuse_check_mysql 2>/dev/null; then
            prereq_ok "MySQL: ${MYSQL_HOST:-127.0.0.1}:${MYSQL_PORT:-3306}/${MYSQL_DATABASE:-bcsfuse_oss}"
        else
            prereq_error "MySQL not reachable (runtime mode requires it)"
            has_error=true
        fi
    fi

    # LLM (dev and runtime, warn only)
    if bcsfuse_check_llm; then
        prereq_ok "LLM: ${LLM_BASE_URL}"
    else
        prereq_warn "LLM_BASE_URL not set — search/recommend/fusion will be limited"
    fi

    # Embedding (dev and runtime, warn only)
    if bcsfuse_check_embedding; then
        prereq_ok "Embedding: ${EMBEDDING_BASE_URL}"
    else
        prereq_warn "EMBEDDING_BASE_URL not set — semantic search will return empty results"
    fi

    if [ "$has_error" = true ]; then
        return 1
    fi
    return 0
}

# ============ Help ============

bcsfuse_help() {
    echo "bcsfuse - Context fusion & semantic search (port ${BCSFUSE_PORT}, mode: ${BCSFUSE_PROVIDER_MODE:-dev})"
}