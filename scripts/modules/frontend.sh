#!/usr/bin/env bash
# scripts/modules/frontend.sh — Frontend service module
[[ -n "${_FRONTEND_SH_LOADED:-}" ]] && return 0
_FRONTEND_SH_LOADED=1

# Explicit composition-root choice; never accept an arbitrary source path.
frontend_select_variant() {
    case "${FRONTEND_VARIANT:-legacy}" in
        legacy)
            FRONTEND_DIR="${PROJECT_ROOT}/src/frontend"
            FRONTEND_DEFAULT_SCRIPT="devs:local:oss"
            FRONTEND_ROOT_ID="root-master"
            ;;
        nextgen)
            FRONTEND_DIR="${PROJECT_ROOT}/src/frontend-nextgen"
            FRONTEND_DEFAULT_SCRIPT="dev:local"
            FRONTEND_ROOT_ID="root"
            ;;
        teamclaw)
            # The internal product UI lives in an external checkout; the dir is
            # an operator input (.env.local), and demanding it here fail-fasts
            # a misconfigured start before anything is built.
            if [ -z "${TEAMCLAW_DIR:-}" ]; then
                printf '%s\n' 'FRONTEND_VARIANT=teamclaw requires TEAMCLAW_DIR (path to the teamclaw checkout, best set in .env.local)' >&2
                return 1
            fi
            if [ ! -d "${TEAMCLAW_DIR}" ]; then
                printf '%s\n' "TEAMCLAW_DIR does not exist: ${TEAMCLAW_DIR}" >&2
                return 1
            fi
            FRONTEND_DIR="${TEAMCLAW_DIR}"
            FRONTEND_DEFAULT_SCRIPT="devs:local"
            FRONTEND_ROOT_ID="root"
            ;;
        *) printf '%s\n' 'FRONTEND_VARIANT must be legacy, nextgen or teamclaw' >&2; return 1 ;;
    esac
}

# teamclaw frontend auto-update: fetch + fast-forward the checkout's current
# branch toward its upstream before the dev server compiles it. Never blocks
# startup: on a dirty or detached checkout it warns and keeps the tree as-is,
# because ff-only protects the operator's uncommitted work, and a pull that
# hides in the start path must never be the thing that ate someone's WIP.
# Set TEAMCLAW_FRONTEND_AUTOUPDATE=0 to fetch-and-report only.
frontend_teamclaw_sync_latest() {
    local dir="${TEAMCLAW_DIR}" branch upstream behind
    [ -n "$dir" ] && [ -d "$dir" ] || return 0
    command -v git >/dev/null 2>&1 || return 0
    git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
        log_warn "TEAMCLAW_DIR is not a git checkout; skipping frontend auto-update"
        return 0
    }
    if ! git -C "$dir" fetch --quiet origin 2>/dev/null; then
        log_warn "teamclaw frontend: git fetch failed; continuing with current tree"
        return 0
    fi
    branch="$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    upstream="$(git -C "$dir" rev-parse --abbrev-ref '@{upstream}' 2>/dev/null || true)"
    if [ -z "${upstream:-}" ] || [ "$upstream" = "HEAD" ]; then
        log_warn "teamclaw frontend: branch '${branch:-unknown}' has no upstream; skipping auto-update"
        return 0
    fi
    behind="$(git -C "$dir" rev-list --count "HEAD..@{upstream}" 2>/dev/null || echo 0)"
    if [ "${behind:-0}" -eq 0 ]; then
        log_info "teamclaw frontend up to date: ${branch}@$(git -C "$dir" rev-parse --short HEAD)"
        return 0
    fi
    if [ "${TEAMCLAW_FRONTEND_AUTOUPDATE:-1}" != "1" ]; then
        log_info "teamclaw frontend is ${behind} commit(s) behind ${upstream} (auto-update disabled); continuing with ${branch}@$(git -C "$dir" rev-parse --short HEAD)"
        return 0
    fi
    if [ -n "$(git -C "$dir" status --porcelain 2>/dev/null)" ]; then
        log_warn "teamclaw frontend is ${behind} commit(s) behind ${upstream}, but the checkout is dirty; not updating. Commit/stash, then run: git -C \"$dir\" merge --ff-only ${upstream}"
        return 0
    fi
    if git -C "$dir" merge --ff-only '@{upstream}' >/dev/null 2>&1; then
        log_info "teamclaw frontend updated to ${branch}@$(git -C "$dir" rev-parse --short HEAD) (was ${behind} behind)"
    else
        log_warn "teamclaw frontend ff-merge failed (diverged?); continuing with ${branch}@$(git -C "$dir" rev-parse --short HEAD)"
    fi
    return 0
}

# Bind the public frontend to the Singlebox Gateway, not the exported
# localhost:8888 placeholder (which is Backend, not Gateway). Other optional
# upstreams remain explicit operator settings; never invent proxy services.
frontend_configure_upstreams() {
    case "${FRONTEND_VARIANT:-legacy}" in
        nextgen|teamclaw) ;;
        *) return 0 ;;
    esac
    export TEAMCLAW_GW_BASE="${TEAMCLAW_GW_BASE:-http://127.0.0.1:${GATEWAY_PORT:-8889}}"
    export TEAMCLAW_ADMIN_BASE="${TEAMCLAW_ADMIN_BASE:-${TEAMCLAW_GW_BASE}}"
    export TASK_ENGINE_UPSTREAM="${TASK_ENGINE_UPSTREAM:-${TEAMCLAW_GW_BASE}}"
    export BCS_ENDPOINT_PRE="${BCS_ENDPOINT_PRE:-http://127.0.0.1:${BCS_PORT:-21000}}"
    export BCS_ENDPOINT_PROD="${BCS_ENDPOINT_PROD:-http://127.0.0.1:${BCS_PORT:-21000}}"
    if [ "${FRONTEND_VARIANT:-legacy}" = teamclaw ]; then
        # Internal-only planes singlebox has no service for (private chat,
        # clawweb, aix harness). Defaulting them to the Gateway makes their
        # panels fail fast (404/502) inside the stack instead of silently
        # targeting an unrelated 8888 placeholder; explicit env always wins.
        export TEAMCLAW_PRIVATE_CHAT_MANAGEMENT_BASE="${TEAMCLAW_PRIVATE_CHAT_MANAGEMENT_BASE:-${TEAMCLAW_GW_BASE}}"
        export TEAMCLAW_PRIVATE_CHAT_SESSION_BASE="${TEAMCLAW_PRIVATE_CHAT_SESSION_BASE:-${TEAMCLAW_GW_BASE}}"
        export TEAMCLAW_LEGACY_AGENTCLAW_BASE="${TEAMCLAW_LEGACY_AGENTCLAW_BASE:-${TEAMCLAW_GW_BASE}}"
        export TEAMCLAW_AIXHARNESS_BASE="${TEAMCLAW_AIXHARNESS_BASE:-${TEAMCLAW_GW_BASE}}"
        export TEAMCLAW_CLAWWEB_BASE="${TEAMCLAW_CLAWWEB_BASE:-${TEAMCLAW_GW_BASE}}"
        # Same local identity the gateway dev_cookie strategy resolves; aligned
        # with /_dev/login's default cookie so header and cookie strategies
        # agree on one user.
        export TEAMCLAW_DEV_USER="${TEAMCLAW_DEV_USER:-001}"
    fi
}

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

    # teamclaw is an external checkout that owns its own branch state: pull it
    # to (or report on) the latest before anything compiles it.
    if [ "${FRONTEND_VARIANT:-legacy}" = teamclaw ]; then
        frontend_teamclaw_sync_latest || true
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
    # the dev command. The internal teamclaw repo drives umi through bigfish,
    # the in-repo frontends through @umijs/max directly.
    local dev_bin="max"
    if [ "${FRONTEND_VARIANT:-legacy}" = teamclaw ]; then
        dev_bin="bigfish"
    fi
    if [ ! -x "${FRONTEND_DIR}/node_modules/.bin/cross-env" ] ||
       [ ! -x "${FRONTEND_DIR}/node_modules/.bin/${dev_bin}" ]; then
        return 1
    fi

    (
        cd "${FRONTEND_DIR}" &&
            node -e '
                const variant = process.argv[1];
                const scope = variant === "nextgen" || variant === "teamclaw" ? "@tc-chat" : "@aix-chat";
                for (const pkg of ["adapters", "core", "ui"].map(name => `${scope}/${name}`)) {
                    require.resolve(`${pkg}/package.json`);
                }
            ' "${FRONTEND_VARIANT:-legacy}"
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
        # --legacy-peer-deps: an un-locked internal dependency graph routinely
        # carries sibling peer ranges (styled-components 5 vs 6 across umi
        # plugins and private extensions) that ERESOLVE on a plain install;
        # teammates' tnpm resolves them leniently and npm must too.
        if ! HUSKY=0 npm install --include=dev --legacy-peer-deps --registry="${NPM_REGISTRY_URL}" --no-audit --no-fund; then
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
    frontend_configure_upstreams
    local frontend_script="${FRONTEND_DEV_SCRIPT:-${FRONTEND_DEFAULT_SCRIPT:-devs:local:oss}}"

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
    printf '%s' "$html" | grep -Fq "id=\"${FRONTEND_ROOT_ID:-root-master}\"" || return 1
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
    echo "frontend - Web UI workbench (port ${FRONTEND_PORT}; FRONTEND_VARIANT=legacy|nextgen)"
}
