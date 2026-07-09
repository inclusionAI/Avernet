#!/usr/bin/env bash
# scripts/modules/bcs.sh — BCS (Bot Coordination Service) module
[[ -n "${_BCS_SH_LOADED:-}" ]] && return 0
_BCS_SH_LOADED=1

# Service-specific constants
DEFAULT_BCS_PORT="21000"
BCS_PORT="${BCS_PORT:-${DEFAULT_BCS_PORT}}"
BCS_LOG="${LOG_DIR}/bcs.log"
BCS_BOTS_STACK_LOG="${LOG_DIR}/bcs_bots_stack.log"
BCS_BOTS_STACK_PID_FILE="${DEP_DIR}/bcs_bots_stack.pid"
BCS_RUNTIME_CONFIG_DIR="${DEP_DIR}/bcs-config"
BCS_PANEL_ASSET_DIR="${BCS_DIR}/assets/panel"
BCS_BOT_PORTS_FILE="${BCS_BOT_PORTS_FILE:-${DEP_DIR}/bcs_bot_ports.env}"
BCS_BOT_PORT_AUTO="${BCS_BOT_PORT_AUTO:-0}"
BOT1_PORT="${BOT1_PORT:-30001}"
BOT2_PORT="${BOT2_PORT:-30011}"
BOT3_PORT="${BOT3_PORT:-30021}"
BOT4_PORT="${BOT4_PORT:-30031}"
BOT5_PORT="${BOT5_PORT:-30041}"

bcs_bot_profile_dir() {
    local profile="$1"
    local root="${OPENCLAW_PROFILE_ROOT:-$HOME}"
    local prefix="${OPENCLAW_PROFILE_PREFIX-.openclaw-}"
    printf '%s/%s%s\n' "$root" "$prefix" "$profile"
}

bcs_port_has_healthy_server() {
    local payload
    payload="$(curl --noproxy '*' --connect-timeout 1 --max-time 2 -s "http://127.0.0.1:${BCS_PORT}/health" 2>/dev/null || true)"
    printf '%s\n' "$payload" | grep -Eq '"service"[[:space:]]*:[[:space:]]*"bcs"'
}

resolve_bcs_config_file() {
    local config_dir="$1"

    if [ -n "${MOLTIS_BCS_CONFIG:-}" ]; then
        echo "${MOLTIS_BCS_CONFIG}"
    elif [ -f "${config_dir}/bcs-config.toml" ]; then
        echo "${config_dir}/bcs-config.toml"
    else
        echo "${config_dir}/bcs-config-local.toml"
    fi
}

toml_sed_replacement() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//&/\\&}"
    value="${value//|/\\|}"
    printf '%s' "$value"
}

prepare_bcs_runtime_config_resources() {
    local config_dir="$1"
    local seeds_src="${BCS_DIR}/seeds"
    local seeds_dst="${config_dir}/seeds"
    local assets_src="${BCS_DIR}/assets"
    local assets_dst="${config_dir}/assets"

    if [ -d "$seeds_src" ]; then
        rm -rf "$seeds_dst"
        mkdir -p "$seeds_dst"
        cp -R "${seeds_src}/." "$seeds_dst/" || return 1
    fi

    rm -rf "$assets_dst"
    if [ -d "$assets_src" ]; then
        mkdir -p "$assets_dst"
        local asset_dir asset_name
        for asset_dir in "${assets_src}"/*; do
            [ -d "$asset_dir" ] || continue
            asset_name="$(basename "$asset_dir")"
            if [ -d "${asset_dir}/dist" ]; then
                mkdir -p "${assets_dst}/${asset_name}"
                cp -R "${asset_dir}/dist" "${assets_dst}/${asset_name}/" || return 1
            else
                log_warn "BCS asset ${asset_name} has no dist directory; skipping runtime asset copy"
            fi
        done
    fi
}

prepare_bcs_runtime_config() {
    local template="${BCS_DIR}/configs/bcs-config-local.toml"
    local config_dir="${BCS_CONFIG_DIR:-${BCS_RUNTIME_CONFIG_DIR}}"
    local base_config="${config_dir}/bcs-config.toml"
    local local_config="${config_dir}/bcs-config-local.toml"
    local bcs_url="http://127.0.0.1:${BCS_PORT}"
    local bcs_bind="${BCS_BIND:-}"
    local bcs_mock_user_id="${BCS_MOCK_USER_ID:-}"
    local bcs_mock_user_name="${BCS_MOCK_USER_NICK_NAME:-}"

    if [ "${BCS_SERVER_ENV:-}" = "dev" ]; then
        if [ -f "${BCS_DIR}/configs/bcs-config-dev.toml" ]; then
            template="${BCS_DIR}/configs/bcs-config-dev.toml"
        elif [ -f "${BCS_DIR}/configs/bcs-config.toml" ]; then
            template="${BCS_DIR}/configs/bcs-config.toml"
        else
            log_warn "BCS dev config not found; falling back to local config template"
        fi
    fi

    if [ ! -f "$template" ]; then
        log_error "BCS config template not found: ${template}"
        return 1
    fi

    mkdir -p "$config_dir"
    cp "$template" "$base_config"
    cp "$template" "$local_config"

    local sed_args=()
    local escaped_bcs_url
    escaped_bcs_url="$(toml_sed_replacement "$bcs_url")"
    # Only rewrite the top-level BCS listener port. Redis/MySQL datasource
    # sections also contain a `port` key and must keep their own values.
    sed_args+=("-e" "1,/^\\[/{s|^port = [0-9][0-9]*$|port = ${BCS_PORT}|;}")
    sed_args+=("-e" "s|^bcs_endpoint = \".*\"$|bcs_endpoint = \"${escaped_bcs_url}\"|")

    if [ -n "$bcs_bind" ]; then
        local escaped_bind
        escaped_bind="$(toml_sed_replacement "$bcs_bind")"
        sed_args+=("-e" "s|^bind = \".*\"$|bind = \"${escaped_bind}\"|")
    fi
    if [ -n "$bcs_mock_user_id" ]; then
        local escaped_mock_user_id
        escaped_mock_user_id="$(toml_sed_replacement "$bcs_mock_user_id")"
        sed_args+=("-e" "s|^mock_user_id = \".*\"$|mock_user_id = \"${escaped_mock_user_id}\"|")
    fi
    if [ -n "$bcs_mock_user_name" ]; then
        local escaped_mock_user_name
        escaped_mock_user_name="$(toml_sed_replacement "$bcs_mock_user_name")"
        sed_args+=("-e" "s|^mock_user_name = \".*\"$|mock_user_name = \"${escaped_mock_user_name}\"|")
    fi

    local config_file tmp_file
    for config_file in "$base_config" "$local_config"; do
        tmp_file="${config_file}.tmp"
        if ! sed "${sed_args[@]}" "$config_file" > "$tmp_file"; then
            rm -f "$tmp_file"
            return 1
        fi
        mv "$tmp_file" "$config_file" || return 1
    done

    prepare_bcs_runtime_config_resources "$config_dir" || return 1

    BCS_CONFIG_DIR="$config_dir"
    export BCS_CONFIG_DIR
    log_info "Prepared BCS runtime config from ${template} -> ${base_config}"
}

# Build BCS, bcs-cli, and bcs-admin.
build_bcs() {
    log_info "Building BCS, bcs-cli, and bcs-admin..."

    # Check Rust is installed
    if ! check_rust_installed; then
        log_error "Rust/Cargo not found. Run: $0 setup"
        return 1
    fi

    # Check protobuf is installed
    if ! check_protobuf_installed; then
        log_error "protobuf not found. Run: $0 setup"
        return 1
    fi

    # Check directory exists
    if ! check_directory_exists "${BCS_DIR}" "bcs"; then
        return 1
    fi

    cd "${BCS_DIR}"

    # Build the binaries required by both the BCS server and the 5-bot local stack.
    if ! cargo build --package bcs --package bcs-cli --package bcs-admin; then
        log_error "Failed to build BCS, bcs-cli, and bcs-admin"
        return 1
    fi

    log_info "BCS, bcs-cli, and bcs-admin built successfully"
}

install_bcs_panel_asset_deps() {
    if [ ! -f "${BCS_PANEL_ASSET_DIR}/package.json" ]; then
        log_info "BCS panel asset package not found, skipping dependency install"
        return 0
    fi

    if ! check_node_available; then
        log_error "Node.js v${REQUIRED_NODE_MAJOR}+ not found. Run: $0 install-tools"
        return 1
    fi
    if ! check_command npm; then
        log_error "npm not found. Install Node.js ${REQUIRED_NODE_MAJOR}+ with npm first."
        return 1
    fi

    if ! bcs_panel_asset_deps_stale; then
        log_info "BCS panel asset dependencies are up to date, skipping npm install"
        return 0
    fi

    log_info "Installing BCS panel asset dependencies..."
    cd "${BCS_PANEL_ASSET_DIR}"

    if [ -f package-lock.json ]; then
        if ! HUSKY=0 npm ci --registry="${NPM_REGISTRY_URL}"; then
            log_error "Failed to install BCS panel asset dependencies"
            return 1
        fi
    elif ! HUSKY=0 npm install --registry="${NPM_REGISTRY_URL}"; then
        log_error "Failed to install BCS panel asset dependencies"
        return 1
    fi

    log_info "BCS panel asset dependencies installed successfully"
}

bcs_panel_asset_deps_stale() {
    local marker="${BCS_PANEL_ASSET_DIR}/node_modules/.package-lock.json"

    if [ ! -d "${BCS_PANEL_ASSET_DIR}/node_modules" ]; then
        return 0
    fi
    if [ ! -f "$marker" ]; then
        return 0
    fi
    if [ "${BCS_PANEL_ASSET_DIR}/package.json" -nt "$marker" ]; then
        return 0
    fi
    if [ -f "${BCS_PANEL_ASSET_DIR}/package-lock.json" ] && [ "${BCS_PANEL_ASSET_DIR}/package-lock.json" -nt "$marker" ]; then
        return 0
    fi

    return 1
}

build_bcs_panel_asset() {
    if [ ! -f "${BCS_PANEL_ASSET_DIR}/package.json" ]; then
        log_info "BCS panel asset package not found, skipping build"
        return 0
    fi

    if ! check_node_available; then
        log_error "Node.js v${REQUIRED_NODE_MAJOR}+ not found. Run: $0 install-tools"
        return 1
    fi
    if ! check_command npm; then
        log_error "npm not found. Install Node.js ${REQUIRED_NODE_MAJOR}+ with npm first."
        return 1
    fi

    if bcs_panel_asset_deps_stale; then
        install_bcs_panel_asset_deps || return 1
    fi

    log_info "Building BCS panel asset..."
    cd "${BCS_PANEL_ASSET_DIR}"

    if ! npm run build; then
        log_error "Failed to build BCS panel asset"
        return 1
    fi

    log_info "BCS panel asset built successfully"
}

bcs_binaries_stale() {
    BCS_BUILD_REASON=""

    local bin
    for bin in \
        "${BCS_DIR}/target/debug/bcs" \
        "${BCS_DIR}/target/debug/bcs-cli" \
        "${BCS_DIR}/target/debug/bcs-admin"; do
        if [ ! -x "$bin" ]; then
            BCS_BUILD_REASON="missing binary: ${bin#${BCS_DIR}/}"
            return 0
        fi
    done

    local source
    while IFS= read -r source; do
        [ -f "$source" ] || continue
        for bin in \
            "${BCS_DIR}/target/debug/bcs" \
            "${BCS_DIR}/target/debug/bcs-cli" \
            "${BCS_DIR}/target/debug/bcs-admin"; do
            if [ "$source" -nt "$bin" ]; then
                BCS_BUILD_REASON="source newer than binary: ${source#${BCS_DIR}/}"
                return 0
            fi
        done
    done < <(
        printf '%s\n' "${BCS_DIR}/Cargo.toml" "${BCS_DIR}/Cargo.lock"
        find "${BCS_DIR}/crates" -type f \( -name '*.rs' -o -name 'Cargo.toml' -o -name 'build.rs' \)
    )

    return 1
}

bcs_port_is_occupied() {
    lsof -tiTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

bcs_port_already_assigned() {
    case " ${_BCS_ASSIGNED_BOT_PORTS:-} " in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}

bcs_load_bot_ports() {
    [ -f "${BCS_BOT_PORTS_FILE}" ] || return 0

    local key value
    while IFS='=' read -r key value; do
        case "$key" in
            BOT1_PORT|BOT2_PORT|BOT3_PORT|BOT4_PORT|BOT5_PORT)
                case "$value" in
                    ''|*[!0-9]*) ;;
                    *) eval "$key=$value" ;;
                esac
                ;;
        esac
    done < "${BCS_BOT_PORTS_FILE}"
}

bcs_save_bot_ports() {
    mkdir -p "$(dirname "${BCS_BOT_PORTS_FILE}")"
    {
        echo "BOT1_PORT=${BOT1_PORT}"
        echo "BOT2_PORT=${BOT2_PORT}"
        echo "BOT3_PORT=${BOT3_PORT}"
        echo "BOT4_PORT=${BOT4_PORT}"
        echo "BOT5_PORT=${BOT5_PORT}"
    } > "${BCS_BOT_PORTS_FILE}"
}

bcs_assign_bot_ports() {
    [ "${BCS_BOT_PORT_AUTO}" = "1" ] || return 0

    _BCS_ASSIGNED_BOT_PORTS=""
    local var label preferred port
    for spec in \
        "BOT1_PORT:CEO" \
        "BOT2_PORT:产品经理" \
        "BOT3_PORT:研发" \
        "BOT4_PORT:验证" \
        "BOT5_PORT:客服"; do
        var="${spec%%:*}"
        label="${spec#*:}"
        eval "preferred=\${$var}"
        port="$preferred"

        while bcs_port_is_occupied "$port" || bcs_port_already_assigned "$port"; do
            port=$((port + 1))
        done

        if [ "$port" != "$preferred" ]; then
            log_warn "${label} port ${preferred} is in use; using ${port} because BCS_BOT_PORT_AUTO=1"
        fi

        eval "$var=$port"
        _BCS_ASSIGNED_BOT_PORTS="${_BCS_ASSIGNED_BOT_PORTS} ${port}"
    done

    bcs_save_bot_ports
}

# Setup BCN plugin (OpenClaw <-> BCS connector)
setup_bcn_plugin() {
    log_info "Setting up BCN plugin (openclaw-channel-bcn)..."

    local mode
    mode="$(bcn_plugin_mode)" || return 1

    local extensions_dir="${OPENCLAW_EXTENSIONS_ROOT:-${HOME}/.openclaw/extensions}"
    local plugin_link="${extensions_dir}/openclaw-channel-bcn"
    local replace_link="${OPENCLAW_EXTENSIONS_REPLACE_LINKS:-0}"

    if [ "$mode" = "npm" ]; then
        local plugin_target
        plugin_target="$(bcn_plugin_ensure_npm)" || return 1
        # Native install may already sit at the link path; avoid self-linking.
        if [ "$plugin_target" = "$plugin_link" ]; then
            log_info "BCN plugin installed at ${plugin_link}"
            return 0
        fi
        ensure_bcn_symlink "$plugin_target" "$plugin_link" "$replace_link"
        return $?
    fi

    # ---- source mode (build from the in-repo tree) ----
    local plugin_src="${PROJECT_ROOT}/src/plugin/packages/openclaw-channel-bcn"

    if [ -d "${plugin_src}" ]; then
        if [ -f "${plugin_src}/dist/esm/index.js" ] && [ -d "${plugin_src}/node_modules" ]; then
            log_info "BCN plugin already built, skipping"
        else
            if ! check_command npm; then
                log_error "npm not found. Install Node.js 22+ with npm before building BCN plugin."
                return 1
            fi

            local work
            work="$(mktemp -d -t openclaw-bcn-build.XXXXXX)"
            log_info "Building BCN plugin with npm in isolated temp dir..."
            log_info "BCN plugin source: ${plugin_src}"
            log_info "Temporary build dir: ${work}"
            log_info "Will replace: ${plugin_src}/dist"
            log_info "Will replace: ${plugin_src}/node_modules"

            if ! cp -R "${plugin_src}/." "${work}/"; then
                rm -rf "${work}"
                log_error "Failed to copy BCN plugin source to temp dir"
                return 1
            fi

            if (
                cd "${work}" &&
                    npm install --registry="${NPM_REGISTRY_URL}" &&
                    npm run build &&
                    npm prune --omit=dev
            ); then
                rm -rf "${plugin_src}/dist" "${plugin_src}/node_modules"
                if ! cp -R "${work}/dist" "${work}/node_modules" "${plugin_src}/"; then
                    rm -rf "${work}"
                    log_error "Failed to copy built BCN plugin artifacts back to source tree"
                    return 1
                fi
                rm -rf "${work}"
            else
                rm -rf "${work}"
                log_error "Failed to build BCN plugin"
                return 1
            fi
        fi
    fi

    if [ ! -d "${plugin_src}" ]; then
        log_warn "BCN plugin source not found at ${plugin_src}, skipping symlink setup"
        log_warn "Per-bot OpenClaw processes will not auto-connect to BCS without the BCN plugin"
        return 0
    fi

    ensure_bcn_symlink "$plugin_src" "$plugin_link" "$replace_link"
    return $?
}

# Wait for BCS health endpoint
wait_for_bcs_health() {
    local max_wait="${1:-60}"
    for _ in $(seq 1 "$max_wait"); do
        if bcs_health_ready; then
            return 0
        fi
        sleep 1
    done
    return 1
}

print_bcs_log_excerpt() {
    local log_file="$1"
    local lines="${2:-40}"

    if [ ! -f "$log_file" ]; then
        log_error "Log file not found: ${log_file}"
        return 0
    fi

    log_error "Recent log excerpt (${log_file}):"
    tail -n "$lines" "$log_file" 2>/dev/null | while IFS= read -r line; do
        log_error "  ${line}"
    done
}

print_bcs_port_owners() {
    local ports="$1"

    if ! command -v lsof >/dev/null 2>&1; then
        return 0
    fi

    local any_owner=false
    local port
    for port in $ports; do
        local owners
        owners="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk -v port="$port" 'NR > 1 {print "port " port ": " $1 " PID=" $2}' | sort -u)"
        if [ -n "$owners" ]; then
            if [ "$any_owner" = false ]; then
                log_error "Current port owners:"
                any_owner=true
            fi
            printf '%s\n' "$owners" | while IFS= read -r line; do
                log_error "  ${line}"
            done
        fi
    done
}

diagnose_bcs_local_stack_failure() {
    local log_file="${1:-${BCS_BOTS_STACK_LOG}}"
    local clean_log
    clean_log="$(mktemp -t bcs-stack-diagnostic.XXXXXX 2>/dev/null || true)"

    if [ ! -f "$log_file" ]; then
        log_error "BCS local stack log not found: ${log_file}"
        return 0
    fi
    if [ -z "$clean_log" ]; then
        print_bcs_log_excerpt "$log_file" 40
        return 0
    fi

    if command -v perl >/dev/null 2>&1; then
        perl -pe 's/\e\[[0-9;]*[A-Za-z]//g' "$log_file" > "$clean_log" 2>/dev/null || cp "$log_file" "$clean_log"
    else
        cp "$log_file" "$clean_log"
    fi

    local port_conflicts
    port_conflicts="$(grep -E 'port [0-9]+ is already in use' "$clean_log" || true)"
    if [ -n "$port_conflicts" ]; then
        log_error "Root cause: required 5bot OpenClaw ports are already occupied."
        printf '%s\n' "$port_conflicts" | sed -E 's/^[[:space:]]*[^[:space:]]+[[:space:]]*//' | while IFS= read -r line; do
            log_error "  ${line}"
        done
        print_bcs_port_owners "30001 30011 30021 30031 30041"
        log_error "Solution: stop the existing 5bot/OpenClaw processes, then rerun $(singlebox_cmd start bots)."
        log_error "  First try: $(singlebox_cmd stop bots)"
        log_error "  If the listed PIDs are stale or from another checkout, stop that checkout or kill those PIDs explicitly."
        rm -f "$clean_log"
        return 0
    fi

    if grep -q 'profile exists but does not match this singlebox local stack' "$clean_log"; then
        log_error "Root cause: existing OpenClaw bot profiles belong to another local stack."
        grep -E 'profile exists but does not match this singlebox local stack|Expected port=' "$clean_log" | while IFS= read -r line; do
            log_error "  ${line}"
        done
        log_error "Solution: prefer an isolated stack: ./scripts/singlebox.sh --standalone restart all"
        log_error "  If those bot profiles are disposable, first clean them with: $(singlebox_cmd clean bots)"
        log_error "  Then rerun: $(singlebox_cmd restart all)"
        rm -f "$clean_log"
        return 0
    fi

    if grep -q 'No config file found' "$clean_log"; then
        log_error "Root cause: BCS did not receive a generated local config file."
        log_error "Solution: rerun $(singlebox_cmd setup bcs), then rerun ./scripts/singlebox.sh $(singlebox_mode_option)."
        print_bcs_log_excerpt "$clean_log" 20
        rm -f "$clean_log"
        return 0
    fi

    if grep -q 'openclaw command not found' "$clean_log"; then
        log_error "Root cause: openclaw command is not available in PATH."
        log_error "Solution: run ./scripts/singlebox.sh install-tools, then rerun ./scripts/singlebox.sh $(singlebox_mode_option)."
        print_bcs_log_excerpt "$clean_log" 20
        rm -f "$clean_log"
        return 0
    fi

    if grep -q 'BCS server failed to start' "$clean_log"; then
        log_error "Root cause: BCS process did not pass /health before timeout."
        log_error "Solution: inspect the BCS server log below, fix the first panic/error, then rerun $(singlebox_cmd start bcs_bots)."
        print_bcs_log_excerpt "${BCS_DIR}/bcs_bots_test_dir/logs/bcs.log" 60
        rm -f "$clean_log"
        return 0
    fi

    if grep -q 'OpenClaw .* failed to start' "$clean_log"; then
        log_error "Root cause: one or more local OpenClaw bot gateways did not pass /health before timeout."
        log_error "Solution: inspect the named bot log under ${BCS_DIR}/bcs_bots_test_dir/logs/, fix the first startup error, then rerun $(singlebox_cmd start bots)."
        print_bcs_log_excerpt "$clean_log" 60
        rm -f "$clean_log"
        return 0
    fi

    print_bcs_log_excerpt "$clean_log" 60
    rm -f "$clean_log"
}

# Wait for all 5 local OpenClaw bots to become ready
wait_for_bcs_local_bots_ready() {
    local max_wait="${1:-120}"
    local bot_specs=(
        "CEO:${BOT1_PORT}:ceo"
        "产品经理:${BOT2_PORT}:product-manager"
        "研发:${BOT3_PORT}:engineering"
        "验证:${BOT4_PORT}:verification"
        "客服:${BOT5_PORT}:customer-service"
    )

    local elapsed=0
    local missing=""
    while [ "$elapsed" -lt "$max_wait" ]; do
        local all_ready=true
        missing=""

        local spec
        for spec in "${bot_specs[@]}"; do
            local name="${spec%%:*}"
            local rest="${spec#*:}"
            local port="${rest%%:*}"
            local profile="${rest#*:}"
            local session_file
            session_file="$(bcs_bot_profile_dir "$profile")/.bcs/session.json"

            if ! port_is_listening "$port"; then
                all_ready=false
                missing="${missing}${name}:port ${port}; "
                continue
            fi

            if ! session_has_token "$session_file"; then
                all_ready=false
                missing="${missing}${name}:token; "
            fi
        done

        if [ "$all_ready" = true ]; then
            return 0
        fi

        if [ "$elapsed" -eq 0 ] || [ $((elapsed % 10)) -eq 0 ]; then
            log_info "Waiting for 5 local OpenClaw bots to become ready: ${missing}"
        fi

        sleep 1
        elapsed=$((elapsed + 1))
    done

    log_warn "5 local OpenClaw bots not ready after ${max_wait}s: ${missing}"
    return 1
}

# Retry onboard for the 5-local-bot stack
run_bcs_local_bots_onboard_with_retry() {
    local stack_script="$1"
    local max_attempts="${BCS_LOCAL_ONBOARD_RETRIES:-3}"
    local ready_timeout="${BCS_LOCAL_BOTS_READY_TIMEOUT:-120}"
    local attempt=1

    case "$max_attempts" in
        ''|*[!0-9]*|0) max_attempts=3 ;;
    esac
    case "$ready_timeout" in
        ''|*[!0-9]*|0) ready_timeout=120 ;;
    esac

    while [ "$attempt" -le "$max_attempts" ]; do
        if wait_for_bcs_local_bots_ready "$ready_timeout"; then
            log_info "Onboarding 5 local OpenClaw bots (attempt ${attempt}/${max_attempts})..."
            if BCS_PORT="${BCS_PORT}" \
                BCS_API_BASE_URL="http://127.0.0.1:${BCS_PORT}" \
                BCS_BOT_PORTS_FILE="${BCS_BOT_PORTS_FILE}" \
                BOT1_PORT="${BOT1_PORT}" \
                BOT2_PORT="${BOT2_PORT}" \
                BOT3_PORT="${BOT3_PORT}" \
                BOT4_PORT="${BOT4_PORT}" \
                BOT5_PORT="${BOT5_PORT}" \
                BCS_AUTH_MOCK="${BCS_AUTH_MOCK}" \
                BCS_MOCK_USER_ID="${BCS_MOCK_USER_ID}" \
                BCS_MOCK_USER_NICK_NAME="${BCS_MOCK_USER_NICK_NAME}" \
                BCS_MOCK_USER_CHANNEL="${BCS_MOCK_USER_CHANNEL}" \
                OPENCLAW_PROFILE_ROOT="${OPENCLAW_PROFILE_ROOT:-}" \
                OPENCLAW_PROFILE_PREFIX="${OPENCLAW_PROFILE_PREFIX-.openclaw-}" \
                OPENCLAW_WORKSPACE_ROOT="${OPENCLAW_WORKSPACE_ROOT:-}" \
                OPENCLAW_WORKSPACE_LAYOUT="${OPENCLAW_WORKSPACE_LAYOUT:-}" \
                OPENCLAW_EXTENSIONS_ROOT="${OPENCLAW_EXTENSIONS_ROOT:-}" \
                OPENCLAW_LOG_ROOT="${OPENCLAW_LOG_ROOT:-}" \
                SINGLEBOX_MODE="${SINGLEBOX_MODE:-local}" \
                bash "$stack_script" onboard >> "${BCS_BOTS_STACK_LOG}" 2>&1; then
                return 0
            fi
            log_warn "5 local OpenClaw bots onboard attempt ${attempt}/${max_attempts} failed"
        else
            log_warn "5 local OpenClaw bots were not ready before onboard attempt ${attempt}/${max_attempts}"
        fi

        if [ "$attempt" -ge "$max_attempts" ]; then
            break
        fi

        log_info "Retrying 5 local OpenClaw bots onboard..."
        sleep 3
        attempt=$((attempt + 1))
    done

    return 1
}

# Onboard default OpenClaw to BCS if connected
onboard_default_openclaw_if_connected() {
    local session_file="${OPENCLAW_CONFIG_DIR}/.bcs/session.json"
    if [ ! -f "$session_file" ]; then
        log_warn "Default OpenClaw BCS session not found: ${session_file}; skipping default OpenClaw onboard"
        return 0
    fi

    local bcs_cli; bcs_cli="$(bcs_cli_path)"
    if [ ! -x "$bcs_cli" ]; then
        log_warn "bcs-cli not found or not executable: ${bcs_cli}; skipping default OpenClaw onboard"
        return 0
    fi

    log_info "Onboarding default OpenClaw to local BCS..."
    BOT_DATA_DIR="${OPENCLAW_CONFIG_DIR}" "$bcs_cli" --url "http://localhost:${BCS_PORT}" onboard \
        --name "${BCS_LOCAL_OPENCLAW_NAME:-OpenClaw Local}" \
        --summary "${BCS_LOCAL_OPENCLAW_SUMMARY:-Local default OpenClaw gateway}" \
        --domains "${BCS_LOCAL_OPENCLAW_DOMAINS:-local,openclaw}" \
        --skills "${BCS_LOCAL_OPENCLAW_SKILLS:-openclaw}" \
        --scopes "${BCS_LOCAL_OPENCLAW_SCOPES:-local}" \
        && log_info "Default OpenClaw onboarded" \
        || log_warn "Default OpenClaw onboard failed; continuing"
}

# Start BCS as a bare binary (dev mode)
start_bcs_binary() {
    mkdir -p "${LOG_DIR}"
    ensure_local_no_proxy

    stop_port_processes_if_owned "${BCS_PORT}" "${PROJECT_ROOT}" "existing BCS"
    if port_is_listening "${BCS_PORT}"; then
        log_error "BCS port ${BCS_PORT} is already in use by a process outside this checkout. Stop it manually or override BCS_PORT."
        return 1
    fi

    log_info "Starting BCS service (SERVER_ENV=${BCS_SERVER_ENV})..."

    if ! check_directory_exists "${BCS_DIR}" "bcs"; then
        return 1
    fi

    # Binary should have been built by bcs_setup; refuse to start if missing
    if ! check_bcs_binary; then
        log_error "BCS binary not found. Run: singlebox.sh setup bcs"
        return 1
    fi

    prepare_bcs_runtime_config || return 1

    cd "${BCS_DIR}"

    # Set environment variables
    load_frontend_bcs_mock_env
    local bcs_config_dir="${BCS_CONFIG_DIR}"
    local bcs_config_file
    bcs_config_file="$(resolve_bcs_config_file "${bcs_config_dir}")"
    export BCS_DATA_DIR="${BCS_DATA_DIR:-${DEP_DIR}/bcs_data}"
    export BCS_CONFIG_DIR="${bcs_config_dir}"
    export MOLTIS_BCS_CONFIG="${bcs_config_file}"
    export BCS_API_BASE_URL="http://127.0.0.1:${BCS_PORT}"
    export RUST_LOG="${RUST_LOG:-info}"
    export MOLTIS_BCS_URL="http://localhost:${BCS_PORT}"
    export BCS_AUTH_MOCK="${BCS_AUTH_MOCK:-1}"
    export BCS_MOCK_USER_ID="${BCS_MOCK_USER_ID:-001}"
    export BCS_MOCK_USER_NICK_NAME="${BCS_MOCK_USER_NICK_NAME:-admin}"
    export BCS_MOCK_USER_CHANNEL="${BCS_MOCK_USER_CHANNEL:-mock}"
    if [ "${BCS_AUTH_MOCK}" = "1" ] && [ -z "${BCS_MOCK_USER_ID}" ]; then
        log_warn "BCS_AUTH_MOCK=1 but BCS_MOCK_USER_ID is empty; caller identity mock is disabled until configured."
    fi

    # Create data directory
    mkdir -p "${BCS_DATA_DIR}"

    # Start BCS service
    local bcs_bin="${BCS_BIN:-./target/debug/bcs}"
    SERVER_ENV="${BCS_SERVER_ENV}" nohup "$bcs_bin" >> "${BCS_LOG}" 2>&1 &
    local bcs_pid=$!

    # Verify process started successfully
    local waited=0
    while [ "$waited" -lt 60 ]; do
        if bcs_health_ready; then
            log_info "BCS started successfully (PID: $bcs_pid) on port ${BCS_PORT}"
            return 0
        fi

        if ! kill -0 "$bcs_pid" 2>/dev/null; then
            break
        fi

        sleep 1
        waited=$((waited + 1))
    done

    log_error "Failed to start BCS. Check logs at ${BCS_LOG}"
    print_bcs_log_excerpt "${BCS_LOG}" 60
    return 1
}

bcs_setup() {
    log_info "Setting up BCS (Bot Coordination Service)..."

    if ! check_directory_exists "${BCS_DIR}" "bcs"; then
        return 1
    fi

    build_bcs_panel_asset || return 1

    if bcs_binaries_stale; then
        log_info "BCS build needed: ${BCS_BUILD_REASON}"
        build_bcs || return 1
        build_bcs_panel_asset || return 1
    else
        log_info "BCS binaries are up to date"
    fi

    log_info "BCS setup complete"
}

bcs_start() {
    resolve_bcs_server_env

    # Auth mock defaults (only for local mode without remote auth cookies)
    if [ "$BCS_SERVER_ENV" = "local" ]; then
        export BCS_AUTH_MOCK="${BCS_AUTH_MOCK:-1}"
        export BCS_MOCK_USER_ID="${BCS_MOCK_USER_ID:-001}"
        export BCS_MOCK_USER_NICK_NAME="${BCS_MOCK_USER_NICK_NAME:-admin}"
    fi

    start_bcs_binary
}

bcs_stop() {
    log_info "Stopping BCS service..."
    mkdir -p "${LOG_DIR}"

    stop_port_processes_if_owned "${BCS_PORT}" "${PROJECT_ROOT}" "BCS"

    log_info "BCS stopped"
}

remove_owned_bcn_plugin_symlink() {
    local plugin_link="${OPENCLAW_EXTENSIONS_ROOT:-${HOME}/.openclaw/extensions}/openclaw-channel-bcn"
    local plugin_src="${PROJECT_ROOT}/src/plugin/packages/openclaw-channel-bcn"
    local npm_dir
    npm_dir="$(bcn_plugin_resolve_npm_dir 2>/dev/null || true)"

    if [ ! -e "$plugin_link" ] && [ ! -L "$plugin_link" ]; then
        log_info "No BCN plugin symlink to remove: ${plugin_link}"
        return 0
    fi

    if [ ! -L "$plugin_link" ]; then
        log_info "BCN plugin link is not a symlink, keeping: ${plugin_link}"
        return 0
    fi

    local current_target
    current_target="$(readlink "$plugin_link")"
    if [ "$current_target" = "$plugin_src" ] || { [ -n "$npm_dir" ] && [ "$current_target" = "$npm_dir" ]; }; then
        rm -f "$plugin_link"
        log_info "Removed BCN plugin symlink: ${plugin_link}"
    else
        log_info "BCN plugin symlink points elsewhere, keeping: ${plugin_link} -> ${current_target}"
    fi
}

bcs_clean() {
    log_info "Cleaning BCS local runtime data..."

    bcs_stop

    local bcs_data="${BCS_DATA_DIR:-${DEP_DIR}/bcs_data}"
    rm -f "${bcs_data}/bcs.db" "${bcs_data}/bcs.db-shm" "${bcs_data}/bcs.db-wal"
    rm -rf "${BCS_RUNTIME_CONFIG_DIR}"

    log_info "BCS local runtime data cleaned"
}

bcs_status() {
    if bcs_health_ready; then
        local bcs_pid
        bcs_pid="$(lsof -tiTCP:"${BCS_PORT}" -sTCP:LISTEN 2>/dev/null | head -1)"
        echo "  BCS:       Running (PID: $bcs_pid, port: ${BCS_PORT})"
    else
        echo "  BCS:       Stopped"
    fi
}

bcs_ready() {
    wait_for_bcs_health 5
}

bcs_cargo_not_found_message() {
    if [ -x "${HOME}/.cargo/bin/cargo" ]; then
        printf 'cargo exists at %s but is not in PATH (required for building BCS). Run: source "%s"\n' \
            "${HOME}/.cargo/bin/cargo" \
            "${HOME}/.cargo/env"
    else
        printf "%s\n" "cargo not found (required for building BCS). Install: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    fi
}

bcs_prereqs() {
    local has_error=false

    echo -e "${CYAN}[bcs] Prerequisites${NC}"

    if [ "${BCS_SKIP_BUILD_PREREQS:-0}" = "1" ] && check_bcs_binary && check_bcs_cli_binary && check_bcs_admin_binary; then
        prereq_ok "BCS binaries already built; skipping cargo/protoc checks"
    else
        # Tool: Rust/Cargo
        if check_rust_installed; then
            prereq_ok "cargo: $(rustc --version 2>&1 | head -1)"
        else
            prereq_error "$(bcs_cargo_not_found_message)"
            has_error=true
        fi

        # Tool: protoc
        if check_protobuf_installed; then
            prereq_ok "protoc: $(get_protobuf_version)"
        else
            prereq_error "protoc not found (required for building BCS). Install: brew install protobuf (macOS)"
            has_error=true
        fi
    fi

    if [ -f "${BCS_PANEL_ASSET_DIR}/package.json" ]; then
        if check_node_available; then
            prereq_ok "node: $(node --version 2>&1)"
        else
            prereq_error "Node.js >= 22 not found (required for BCS panel asset). Install: brew install node@22 (macOS)"
            has_error=true
        fi

        if check_command npm; then
            prereq_ok "npm: $(npm --version 2>&1)"
        else
            prereq_error "npm not found (required for BCS panel asset). Install Node.js 22+ with npm."
            has_error=true
        fi
    fi

    # Directory: BCS_DIR
    if [ -d "${BCS_DIR}" ]; then
        prereq_ok "directory: ${BCS_DIR}"
    else
        prereq_error "directory not found: ${BCS_DIR}"
        has_error=true
    fi

    # Port
    if check_port_available "${BCS_PORT}"; then
        prereq_ok "Port ${BCS_PORT} available"
    elif [ "${SINGLEBOX_COMMAND:-}" = "start" ] && bcs_port_has_healthy_server; then
        prereq_ok "Port ${BCS_PORT} already has a healthy BCS server; start will refresh the local BCS runtime"
    else
        if [ "${SINGLEBOX_COMMAND:-}" = "start" ]; then
            local listener suggested_port
            listener="$(port_listener_summary "${BCS_PORT}")"
            suggested_port="$(find_available_port_near "${BCS_PORT}" 100 || true)"
            if [ -z "$suggested_port" ]; then
                case "$BCS_PORT" in
                    ''|*[!0-9]*) suggested_port="${DEFAULT_BCS_PORT}" ;;
                    *) suggested_port="$((BCS_PORT + 1))" ;;
                esac
            fi

            if [ "${STANDALONE_MODE:-false}" = true ]; then
                prereq_error "BCS port ${BCS_PORT} is in use.
    Current listener: ${listener}
    Stop this module and retry:
      ./scripts/singlebox.sh --standalone stop bcs
      ./scripts/singlebox.sh --standalone
    Or choose another BCS port:
      ./scripts/singlebox.sh --standalone --bcs-port ${suggested_port}"
                print_port_conflict_guidance "${BCS_PORT}" "${PROJECT_ROOT}" "BCS" "./scripts/singlebox.sh --standalone stop bcs" "./scripts/singlebox.sh --standalone --bcs-port ${suggested_port}"
            else
                prereq_error "BCS port ${BCS_PORT} is in use.
    Current listener: ${listener}
    Stop this module and retry:
      ./scripts/singlebox.sh stop bcs
      ./scripts/singlebox.sh start bcs
    Or choose another BCS port:
      ./scripts/singlebox.sh --bcs-port ${suggested_port} start bcs"
                print_port_conflict_guidance "${BCS_PORT}" "${PROJECT_ROOT}" "BCS" "./scripts/singlebox.sh stop bcs" "./scripts/singlebox.sh --bcs-port ${suggested_port} start bcs"
            fi
            has_error=true
        else
            prereq_warn "Port ${BCS_PORT} is in use"
            print_port_conflict_guidance "${BCS_PORT}" "${PROJECT_ROOT}" "BCS" "$(singlebox_cmd stop bcs)" "set BCS_PORT=<free-port> in .env.local" false
        fi
    fi

    if [ "$has_error" = true ]; then
        return 1
    fi
    return 0
}

bcs_help() {
    echo "bcs - BCS (Bot Coordination Service) (port ${BCS_PORT})"
}
