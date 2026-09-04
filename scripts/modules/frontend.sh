#!/usr/bin/env bash
# scripts/modules/frontend.sh — Frontend service module
[[ -n "${_FRONTEND_SH_LOADED:-}" ]] && return 0
_FRONTEND_SH_LOADED=1

# Service-specific constants
FRONTEND_LOG="${LOG_DIR}/frontend.log"
# 前端 dev server 端口（umi 读 PORT 环境变量）。默认 8000，可用 FRONTEND_PORT 覆盖；
# 由 singlebox.sh 经 .env.local / --frontend-port 注入。
FRONTEND_PORT="${FRONTEND_PORT:-8000}"
FRONTEND_PID_FILE="${DEP_DIR}/frontend.pid"

frontend_setup() {
    log_info "Setting up open-claw frontend..."

    if ! check_directory_exists "${FRONTEND_DIR}" "frontend"; then
        return 1
    fi

    # Check toolchain
    if ! check_node_available; then
        log_error "Node.js v${REQUIRED_NODE_MAJOR}+ not found. Run: singlebox.sh install-tools"
        return 1
    fi
    if ! check_command npm; then
        log_error "npm not found. Install Node.js with npm first."
        return 1
    fi

    cd "${FRONTEND_DIR}"

    if frontend_deps_ready; then
        log_info "Frontend dependencies are up to date, skipping npm install"
    else
        if [ -d "${FRONTEND_DIR}/node_modules" ]; then
            log_warn "Frontend dependencies are missing or stale; reinstalling"
        fi
        install_frontend_deps || return 1
    fi

    log_info "Frontend ready"
}

frontend_deps_ready() {
    local marker="${FRONTEND_DIR}/node_modules/.package-lock.json"

    if [ ! -d "${FRONTEND_DIR}/node_modules" ]; then
        return 1
    fi
    if [ ! -f "$marker" ]; then
        return 1
    fi
    if [ "${FRONTEND_DIR}/package.json" -nt "$marker" ]; then
        return 1
    fi
    if [ -f "${FRONTEND_DIR}/package-lock.json" ] && [ "${FRONTEND_DIR}/package-lock.json" -nt "$marker" ]; then
        return 1
    fi
    # The local dev command is provided by devDependencies. A production-only
    # install can still contain all runtime packages while being unable to run
    # `cross-env ... max dev`.
    if [ ! -x "${FRONTEND_DIR}/node_modules/.bin/cross-env" ] ||
       [ ! -x "${FRONTEND_DIR}/node_modules/.bin/max" ]; then
        return 1
    fi

    (
        cd "${FRONTEND_DIR}" &&
            node -e '
                for (const pkg of ["@aix-chat/adapters", "@aix-chat/core", "@aix-chat/ui"]) {
                    require.resolve(`${pkg}/package.json`);
                }
            '
    ) >/dev/null 2>&1
}

# Install frontend dependencies
install_frontend_deps() {
    log_info "Installing frontend dependencies..."
    cd "${FRONTEND_DIR}"

    # 有 lockfile 时用 npm ci:严格按 lockfile 安装,且不改写 lockfile —— 保证可复现,
    # 也不会把 resolved 源改回内网镜像(license 合规要求:committed lockfile 恒为公网源)。
    # 用 --registry 仅影响下载来源,npm ci 不会回写 lockfile。
    # 前端 dev server 依赖 cross-env 和 @umijs/max 等 devDependencies，显式包含 dev
    # 依赖，避免 NODE_ENV=production 或 npm omit 配置导致启动命令缺失。
    # --no-audit/--no-fund: npm's legacy audit endpoint is being retired and
    # the call now stalls for ~7m before giving up, a fixed cost per
    # invocation regardless of tree size (see install_bcs_panel_asset_deps in
    # bcs.sh for the measurement). Neither flag changes what is installed.
    if [ -f package-lock.json ]; then
        if ! HUSKY=0 npm ci --include=dev --registry="${NPM_REGISTRY_URL}" --no-audit --no-fund; then
            log_error "Failed to install frontend dependencies (npm ci)."
            log_error "若刚改过 package.json,请先本地 'npm install' 更新 package-lock.json 再提交。"
            return 1
        fi
    else
        log_warn "No package-lock.json; falling back to 'npm install' (will generate a lockfile)."
        if ! HUSKY=0 npm install --include=dev --registry="${NPM_REGISTRY_URL}" --no-audit --no-fund; then
            log_error "Failed to install frontend dependencies"
            return 1
        fi
    fi

    log_info "Frontend dependencies installed successfully"
}

frontend_start() {
    mkdir -p "${LOG_DIR}"

    # `start` may be invoked without a preceding `setup`. Ensure dependencies
    # once here; frontend_setup skips npm install when the lockfile marker is
    # already current.
    frontend_setup || return 1

    cd "${FRONTEND_DIR}"
    local frontend_script="${FRONTEND_DEV_SCRIPT:-devs:local:oss}"

    stop_port_processes_if_owned "${FRONTEND_PORT}" "${FRONTEND_DIR}" "existing frontend"
    if port_is_listening "${FRONTEND_PORT}"; then
        log_error "Port ${FRONTEND_PORT} is already in use by a process outside this checkout. Stop it manually or choose another port."
        return 1
    fi

    # bigfish/umi spawns tailwindcss --watch with stdio:"inherit".
    # tailwindcss v3 listens for process.stdin "end" event in --watch mode;
    # when stdin is /dev/null (from nohup), the "end" fires immediately,
    # causing tailwindcss to exit(0) before generating output, which makes
    # the umi plugin timeout after 5s and crash-loop the dev server.
    # Fix: pipe a long-lived stdin source that never closes, so stdin "end"
    # never fires.  `tail -f /dev/null` never outputs and never exits.
    log_info "Starting frontend service (background mode)..."
    log_info "Script: npm run ${frontend_script}"
    log_info "Log: ${FRONTEND_LOG}"
    # Propagate BCS_PORT/BCSFUSE_PORT so the dev proxy (config.local.ts) targets
    # the same ports the BCS backend actually binds — explicit passing covers the
    # default-assignment case where the vars aren't exported.
    # PORT controls the umi dev server listen port (default 8000, via FRONTEND_PORT).
    BCS_PORT="${BCS_PORT}" BCSFUSE_PORT="${BCSFUSE_PORT}" PORT="${FRONTEND_PORT}" \
        start_in_detached_session bash -c 'exec 0< <(tail -f /dev/null); exec npm run "$1"' -- "$frontend_script" >> "${FRONTEND_LOG}" 2>&1 &
    local frontend_pid=$!
    echo "$frontend_pid" > "${FRONTEND_PID_FILE}"
    log_info "Process started (PID: ${frontend_pid})"

    # Umi may return a 200 "Bundling..." placeholder before the app shell is
    # ready. Wait for the real app HTML plus the main bundle endpoint.
    local waited=0
    while [ "$waited" -lt 240 ]; do
        if frontend_http_ready; then
            local frontend_pid
            frontend_pid="$(lsof -tiTCP:${FRONTEND_PORT} -sTCP:LISTEN | head -1)"
            log_info "Frontend ready on port ${FRONTEND_PORT} (PID: ${frontend_pid})"
            if [ "${SINGLEBOX_DEFER_FRONTEND_READY_HINT:-0}" = "1" ]; then
                log_info "Frontend URL: http://localhost:${FRONTEND_PORT}/"
            else
                print_frontend_ready_banner
            fi
            return 0
        fi
        if [ "$waited" -eq 0 ] || [ $((waited % 20)) -eq 0 ]; then
            log_info "Waiting for frontend app shell to finish on port ${FRONTEND_PORT}..."
        fi
        sleep 0.5
        waited=$((waited + 1))
    done
    log_warn "Frontend did not serve the app shell and /umi.js on port ${FRONTEND_PORT} within 120s; check ${FRONTEND_LOG}"
    log_warn "Hint: tail -f ${FRONTEND_LOG}"
    return 1
}

frontend_http_ready() {
    local html
    html="$(curl --noproxy '*' --connect-timeout 1 --max-time 2 -fsS "http://127.0.0.1:${FRONTEND_PORT}/" 2>/dev/null)" || return 1
    printf '%s' "$html" | grep -q 'id="root-master"' || return 1
    printf '%s' "$html" | grep -q 'src="/umi.js"' || return 1
    if printf '%s' "$html" | grep -qi 'Bundling'; then
        return 1
    fi
    curl --noproxy '*' --connect-timeout 1 --max-time 2 -fsSI "http://127.0.0.1:${FRONTEND_PORT}/umi.js" >/dev/null 2>&1
}

frontend_ready() {
    frontend_http_ready
}

frontend_stop() {
    log_info "Stopping frontend..."

    if [ -f "${FRONTEND_PID_FILE}" ]; then
        local frontend_pid
        frontend_pid="$(cat "${FRONTEND_PID_FILE}" 2>/dev/null || true)"
        if [ -n "$frontend_pid" ]; then
            stop_process_if_owned "$frontend_pid" "${FRONTEND_DIR}" "frontend pidfile process" || true
        fi
        rm -f "${FRONTEND_PID_FILE}"
    fi

    stop_port_processes_if_owned "${FRONTEND_PORT}" "${FRONTEND_DIR}" "frontend"
    stop_matching_processes_if_owned "tail -f /dev/null" "${FRONTEND_DIR}" "frontend stdin keeper"
    log_info "Frontend stopped"
}

frontend_status() {
    local frontend_pid=$(lsof -tiTCP:${FRONTEND_PORT} -sTCP:LISTEN 2>/dev/null | head -1)
    if [ -n "$frontend_pid" ]; then
        echo "  Frontend:  Running (PID: $frontend_pid)"
    else
        echo "  Frontend:  Stopped"
    fi
}

frontend_prereqs() {
    local has_error=false

    echo -e "${CYAN}[frontend] Prerequisites${NC}"

    if check_node_available; then
        prereq_ok "Node.js v${REQUIRED_NODE_MAJOR}+ found"
    else
        prereq_error "Node.js ${REQUIRED_NODE_MAJOR}+ not found"
        has_error=true
    fi

    if check_command npm; then
        prereq_ok "npm found"
    else
        prereq_error "npm not found. Install Node.js with npm first."
        has_error=true
    fi

    if [ -d "${FRONTEND_DIR}" ]; then
        prereq_ok "directory: ${FRONTEND_DIR}"
    else
        prereq_error "directory not found: ${FRONTEND_DIR}"
        has_error=true
    fi

    if check_port_available ${FRONTEND_PORT}; then
        prereq_ok "Port ${FRONTEND_PORT} available"
    else
        prereq_warn "Port ${FRONTEND_PORT} is in use"
        print_port_conflict_guidance "${FRONTEND_PORT}" "${FRONTEND_DIR}" "frontend" "$(singlebox_cmd stop frontend)" "set FRONTEND_PORT=<free-port> in .env.local" false
    fi

    if [ "$has_error" = true ]; then
        return 1
    fi
    return 0
}

frontend_help() {
    echo "frontend - Web UI workbench (port ${FRONTEND_PORT})"
}
