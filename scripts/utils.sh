#!/usr/bin/env bash
# scripts/utils.sh — Shared utility functions for singlebox.sh
# Pure functions, no side effects beyond logging. No service-specific logic.
[[ -n "${_UTILS_SH_LOADED:-}" ]] && return 0
_UTILS_SH_LOADED=1

# ============ 颜色输出 ============
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

singlebox_mode_option() {
    if [ "${SINGLEBOX_MODE:-standalone}" = "standalone" ] || [ "${STANDALONE_MODE:-true}" = true ]; then
        echo "--standalone"
    else
        echo "--local"
    fi
}

singlebox_cmd() {
    local action="$1"
    local target="$2"
    echo "./scripts/singlebox.sh $(singlebox_mode_option) ${action} ${target}"
}

print_frontend_ready_banner() {
    echo ""
    echo -e "${GREEN}============================================================${NC}"
    echo -e "${GREEN}  FRONTEND READY${NC}"
    echo -e "${CYAN}  Open the workbench:${NC}"
    echo -e "${CYAN}  http://localhost:${FRONTEND_PORT:-8000}/${NC}"
    echo -e "${GREEN}============================================================${NC}"
    echo ""
}

print_banner_line() {
    local color="$1"
    local text="$2"
    printf "%b| %-58s |%b\n" "$color" "$text" "$NC"
}

singlebox_display_path() {
    local path="$1"
    case "$path" in
        "${PROJECT_ROOT}/"*)
            printf '%s\n' "${path#${PROJECT_ROOT}/}"
            ;;
        "${HOME}/"*)
            printf '~/%s\n' "${path#${HOME}/}"
            ;;
        *)
            printf '%s\n' "$path"
            ;;
    esac
}

singlebox_5bot_profile_pattern() {
    local root="${OPENCLAW_PROFILE_ROOT:-$HOME}"
    local prefix="${OPENCLAW_PROFILE_PREFIX-.openclaw-}"
    local profiles="{ceo,...}"

    if [ -n "$prefix" ]; then
        printf '%s/%s%s\n' "$root" "$prefix" "$profiles"
    else
        printf '%s/%s\n' "$root" "$profiles"
    fi
}

singlebox_5bot_workspace_pattern() {
    local root="${OPENCLAW_WORKSPACE_ROOT:-${BCS_DIR}/bcs_bots_test_dir}"

    case "${OPENCLAW_WORKSPACE_LAYOUT:-profile-source}" in
        profile)
            printf '%s/{ceo,...}\n' "$root"
            ;;
        profile-source)
            printf '%s/{ceo,...}/workspace\n' "$root"
            ;;
        *)
            printf '%s/{CEO,产品经理,研发,验证,客服}/workspace\n' "$root"
            ;;
    esac
}

print_local_stack_runtime_paths() {
    local bot_log_dir="${OPENCLAW_LOG_ROOT:-${BCS_DIR}/bcs_bots_test_dir/logs}"
    local bcs_data_dir="${BCS_DATA_DIR:-${DEP_DIR}/bcs_data}"
    local bcs_config_dir="${BCS_RUNTIME_CONFIG_DIR:-${DEP_DIR}/bcs-config}"

    print_banner_line "$CYAN" "Workbench  http://localhost:${FRONTEND_PORT:-8000}/"
    print_banner_line "$CYAN" "BCS data   $(singlebox_display_path "$bcs_data_dir")"
    print_banner_line "$CYAN" "BCS config $(singlebox_display_path "$bcs_config_dir")"
    print_banner_line "$CYAN" "Logs       $(singlebox_display_path "$LOG_DIR")"
    print_banner_line "$CYAN" "5bot logs  $(singlebox_display_path "$bot_log_dir")"

    if [ "${LOCAL_MODE:-true}" != true ]; then
        return
    fi

    print_banner_line "$CYAN" "Profiles   $(singlebox_display_path "$(singlebox_5bot_profile_pattern)")"
    print_banner_line "$CYAN" "Workspace  $(singlebox_display_path "$(singlebox_5bot_workspace_pattern)")"
}

print_local_stack_ready_banner() {
    local title="LOCAL STACK READY"
    local stack_label="FULL SINGLEBOX STACK"
    local status_line_one="BAAS BACKEND BCS"
    local status_line_two="5BOTS DEMO FRONTEND"

    if [ "${STANDALONE_MODE:-false}" = true ]; then
        title="STANDALONE STACK READY"
    fi
    if [ "${LOCAL_MODE:-true}" != true ]; then
        stack_label="BCS + FRONTEND"
        status_line_one="BCS FRONTEND"
        status_line_two=""
    fi

    echo ""
    echo -e "${GREEN}+============================================================+${NC}"
    print_banner_line "$GREEN" ""
    print_banner_line "$GREEN" "  ____   ____  ____     ${title}"
    print_banner_line "$GREEN" " / __ \\ / ___|| __ )    ${stack_label}"
    print_banner_line "$GREEN" "| |  | | |    |  _ \\    ${status_line_one}"
    print_banner_line "$GREEN" "| |__| | |___ | |_) |   ${status_line_two}"
    print_banner_line "$GREEN" " \\____/ \\____||____/"
    print_banner_line "$GREEN" ""
    echo -e "${GREEN}+------------------------------------------------------------+${NC}"
    print_local_stack_runtime_paths
    echo -e "${GREEN}+============================================================+${NC}"
    echo ""
}

# ============ 端口/进程工具 ============

canonical_dir() {
    local dir="$1"
    (cd "$dir" 2>/dev/null && pwd -P) || echo "$dir"
}

path_is_under_dir() {
    local path="$1"
    local dir="$2"
    local canonical_path
    local canonical_root

    canonical_path="$(canonical_dir "$path")"
    canonical_root="$(canonical_dir "$dir")"

    case "$canonical_path" in
        "$canonical_root"|"$canonical_root"/*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

process_cwd() {
    local pid="$1"
    lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1
}

process_command() {
    local pid="$1"
    ps -p "$pid" -o command= 2>/dev/null || true
}

describe_process() {
    local pid="$1"
    local cwd
    local command

    cwd="$(process_cwd "$pid")"
    command="$(process_command "$pid")"
    echo "PID ${pid}, cwd=${cwd:-unknown}, command=${command:-unknown}"
}

terminate_process() {
    local pid="$1"
    local label="${2:-process}"
    local waited=0

    if ! kill -0 "$pid" 2>/dev/null; then
        return 0
    fi

    log_info "Stopping ${label} ($(describe_process "$pid"))"
    kill "$pid" 2>/dev/null || true
    while [ "$waited" -lt 5 ]; do
        if ! kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done

    if kill -0 "$pid" 2>/dev/null; then
        log_warn "${label} did not exit after SIGTERM; force killing PID ${pid}"
        kill -9 "$pid" 2>/dev/null || true
    fi
}

stop_process_if_owned() {
    local pid="$1"
    local owner_dir="$2"
    local label="${3:-process}"
    local cwd

    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
        return 0
    fi

    cwd="$(process_cwd "$pid")"
    if [ -z "$cwd" ]; then
        log_warn "Skipping ${label} PID ${pid}: cannot verify process cwd"
        return 1
    fi

    if path_is_under_dir "$cwd" "$owner_dir"; then
        terminate_process "$pid" "$label"
        return 0
    fi

    log_warn "Skipping ${label} PID ${pid}: process is outside current checkout (cwd=${cwd})"
    return 1
}

stop_port_processes_if_owned() {
    local port="$1"
    local owner_dir="$2"
    local label="${3:-process}"
    local pids
    local pid

    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    if [ -z "$pids" ]; then
        return 0
    fi

    for pid in $pids; do
        stop_process_if_owned "$pid" "$owner_dir" "${label} on port ${port}" || true
    done
}

require_port_available_after_owned_stop() {
    local port="$1"
    local service_name="$2"
    local override_hint="${3:-choose another port}"

    if ! port_is_listening "$port"; then
        return 0
    fi

    log_error "Port ${port} is still in use by a process outside this checkout after stopping owned ${service_name} processes."
    log_error "Owner: $(port_listener_summary "$port")"
    log_error "Stop the external process manually or ${override_hint}."
    return 1
}

stop_matching_processes_if_owned() {
    local pattern="$1"
    local owner_dir="$2"
    local label="${3:-process}"
    local pids
    local pid

    pids="$(ps ax -o pid= -o command= 2>/dev/null | awk -v pat="$pattern" 'index($0, pat) {print $1}')"
    if [ -z "$pids" ]; then
        return 0
    fi

    for pid in $pids; do
        stop_process_if_owned "$pid" "$owner_dir" "$label" || true
    done
}

# 杀掉占用指定端口的进程
kill_port_process() {
    local port="$1"
    local pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null)

    if [ -n "$pids" ]; then
        log_warn "Killing process on port ${port} (PIDs: ${pids})"
        echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
}

# 根据路径杀进程
kill_process_by_path() {
    local bin_path="$1"
    local pids=$(ps aux | grep "${bin_path}" | grep -v grep | awk '{print $2}')

    if [ -n "$pids" ]; then
        log_warn "Killing process: ${bin_path} (PIDs: ${pids})"
        echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
}

# 检查端口是否在监听
port_is_listening() {
    local port="$1"
    lsof -nP -iTCP:"$port" -sTCP:LISTEN > /dev/null 2>&1
}

port_listener_summary() {
    local port="$1"
    local line pid="" cmd="" summary=""

    while IFS= read -r line; do
        case "$line" in
            p*)
                if [ -n "$pid" ]; then
                    [ -n "$cmd" ] || cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
                    [ -n "$cmd" ] || cmd="unknown"
                    if [ -n "$summary" ]; then
                        summary="${summary}; "
                    fi
                    summary="${summary}PID ${pid}, command: ${cmd}"
                fi
                pid="${line#p}"
                cmd=""
                ;;
            c*)
                cmd="${line#c}"
                ;;
        esac
    done < <(lsof -nP -iTCP:"$port" -sTCP:LISTEN -Fp -Fc 2>/dev/null || true)

    if [ -n "$pid" ]; then
        [ -n "$cmd" ] || cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
        [ -n "$cmd" ] || cmd="unknown"
        if [ -n "$summary" ]; then
            summary="${summary}; "
        fi
        summary="${summary}PID ${pid}, command: ${cmd}"
    fi

    if [ -z "$summary" ]; then
        summary="unknown"
    fi

    echo "$summary"
}

find_available_port_near() {
    local start_port="$1"
    local max_scan="${2:-100}"
    local port
    local scanned=0

    case "$start_port" in
        ''|*[!0-9]*) return 1 ;;
    esac
    case "$max_scan" in
        ''|*[!0-9]*) max_scan=100 ;;
    esac

    port=$((start_port + 1))
    while [ "$scanned" -lt "$max_scan" ]; do
        if ! port_is_listening "$port"; then
            echo "$port"
            return 0
        fi
        port=$((port + 1))
        scanned=$((scanned + 1))
    done

    return 1
}

# 检查 session 文件是否有 token
session_has_token() {
    local session_file="$1"
    [ -f "$session_file" ] && grep -Eq '"token"[[:space:]]*:[[:space:]]*"[^"]+"' "$session_file"
}

# ============ 目录工具 ============

# 检查目录是否存在
check_directory_exists() {
    local dir_path="$1"
    local dir_name="$2"

    if [ -d "${dir_path}" ]; then
        return 0
    else
        log_error "${dir_name} directory not found at ${dir_path}"
        return 1
    fi
}

# ============ 架构检测 ============

# 获取系统架构标识 (os-arch)
detect_arch() {
    local os=$(uname -s | tr '[:upper:]' '[:lower:]')
    local arch=$(uname -m)

    # 标准化架构名称
    case "$arch" in
        arm64|aarch64) arch="arm64" ;;
        x86_64|amd64)  arch="amd64" ;;
        *)
            log_error "Unsupported architecture: $arch"
            exit 1
            ;;
    esac

    echo "${os}-${arch}"
}

# Get current architecture (simple)
get_arch() {
    uname -m
}

# 检查是否在 macOS ARM64 上运行（包括 Rosetta 环境）
is_macos_arm64() {
    # 实际 ARM64 CPU（非 Rosetta）
    [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] && return 0
    # Rosetta 环境（物理 ARM64 但显示 x86_64）
    [[ "$(uname -s)" == "Darwin" && "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" == "1" ]] && return 0
    return 1
}

# ============ 网络工具 ============

# 确保 localhost 不走代理
ensure_local_no_proxy() {
    local host
    for host in localhost 127.0.0.1 ::1; do
        case ",${NO_PROXY:-}," in
            *",${host},"*) ;;
            *) NO_PROXY="${NO_PROXY:+${NO_PROXY},}${host}" ;;
        esac
        case ",${no_proxy:-}," in
            *",${host},"*) ;;
            *) no_proxy="${no_proxy:+${no_proxy},}${host}" ;;
        esac
    done
    export NO_PROXY no_proxy
}

# ============ 环境工具 ============

# 从 frontend .env.local 加载 BCS mock 环境变量
load_frontend_bcs_mock_env() {
    local env_file="${FRONTEND_DIR}/.env.local"
    if [ ! -f "$env_file" ]; then
        return 0
    fi

    while IFS='=' read -r key value || [ -n "$key" ]; do
        case "$key" in
            BCS_AUTH_MOCK|BCS_MOCK_USER_ID|BCS_MOCK_USER_NICK_NAME|BCS_MOCK_USER_CHANNEL)
                value="${value%%#*}"
                value="${value%$'\r'}"
                value="${value#"${value%%[![:space:]]*}"}"
                value="${value%"${value##*[![:space:]]}"}"
                value="${value%\"}"
                value="${value#\"}"
                value="${value%\'}"
                value="${value#\'}"
                if [ -z "${!key:-}" ] && [ -n "$value" ]; then
                    export "$key=$value"
                fi
                ;;
        esac
    done < "$env_file"
}

# ============ 引擎类型管理 ============

# 保存引擎类型到状态文件
save_engine_type() {
    mkdir -p "${LOG_DIR}"
    echo "${CHAT_ENGINE}" > "${ENGINE_STATE_FILE}"
}

# 从状态文件读取引擎类型
load_engine_type() {
    if [ -f "${ENGINE_STATE_FILE}" ]; then
        local saved_engine=$(cat "${ENGINE_STATE_FILE}" 2>/dev/null | tr -d '[:space:]')
        if [ -n "$saved_engine" ] && [ "$saved_engine" != "${CHAT_ENGINE}" ]; then
            log_info "Restoring engine type from state file: $saved_engine"
            CHAT_ENGINE="$saved_engine"
        fi
    fi
}

# Resolve the underlying engine service name from CHAT_ENGINE.
# Returns the module name (openclaw | hermes | relay), or "openclaw" as default.
# Used by baas_setup/baas_prereqs and all_stop to dispatch per-engine actions.
_resolve_engine_svc() {
    case "${CHAT_ENGINE}" in
        openclaw)  echo "openclaw" ;;
        hermes)    echo "hermes" ;;
        aicoding)  echo "relay" ;;
        *)         echo "openclaw" ;;
    esac
}

# ============ 命令检查 ============

# 检查命令是否存在
check_command() {
    if command -v "$1" &> /dev/null; then
        return 0
    else
        return 1
    fi
}

# ============ 前置检查辅助 (用于 check 命令) ============

PREREQ_ERRORS=()
PREREQ_WARNINGS=()
PREREQ_SOLUTIONS=()

prereq_error() {
    PREREQ_ERRORS+=("$1")
    echo -e "  ${RED}✗${NC} $1"
}

prereq_warn() {
    PREREQ_WARNINGS+=("$1")
    echo -e "  ${YELLOW}⚠${NC} $1"
}

prereq_ok() {
    echo -e "  ${GREEN}✓${NC} $1"
}

prereq_hint() {
    echo -e "    ${CYAN}→${NC} $1"
}

prereq_solution() {
    local solution="$1"
    local existing

    for existing in "${PREREQ_SOLUTIONS[@]}"; do
        if [ "$existing" = "$solution" ]; then
            return 0
        fi
    done

    PREREQ_SOLUTIONS+=("$solution")
}

print_port_conflict_guidance() {
    local port="$1"
    local owner_dir="$2"
    local service_name="$3"
    local stop_cmd="$4"
    local override_hint="${5:-}"
    local record_solution="${6:-true}"
    local pids
    local pid
    local cwd

    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    if [ -z "$pids" ]; then
        return 0
    fi

    for pid in $pids; do
        cwd="$(process_cwd "$pid")"

        if [ -n "$cwd" ] && path_is_under_dir "$cwd" "$owner_dir"; then
            prereq_hint "Owner: PID ${pid}, current checkout. Fix: ${stop_cmd}"
            if [ "$record_solution" != "false" ]; then
                prereq_solution "${service_name}: ${stop_cmd}"
            fi
        elif [ -n "$cwd" ]; then
            if [ -n "$override_hint" ]; then
                prereq_hint "Owner: PID ${pid}, outside current checkout (cwd=${cwd})."
                if [ "$record_solution" != "false" ]; then
                    prereq_solution "${service_name}: stop PID ${pid}, or ${override_hint}"
                fi
            else
                prereq_hint "Owner: PID ${pid}, outside current checkout (cwd=${cwd})."
                if [ "$record_solution" != "false" ]; then
                    prereq_solution "${service_name}: stop PID ${pid} before starting this checkout"
                fi
            fi
        else
            if [ -n "$override_hint" ]; then
                prereq_hint "Owner: PID ${pid}, ownership unknown."
                if [ "$record_solution" != "false" ]; then
                    prereq_solution "${service_name}: inspect PID ${pid}, or ${override_hint}"
                fi
            else
                prereq_hint "Owner: PID ${pid}, ownership unknown."
                if [ "$record_solution" != "false" ]; then
                    prereq_solution "${service_name}: inspect PID ${pid} before killing it"
                fi
            fi
        fi
    done
}

print_prereq_failure_guidance() {
    local command="${SINGLEBOX_COMMAND:-command}"

    case "$command" in
        start)
            log_error "Start blocked: fix ERRORS found before rerunning."
            ;;
        setup)
            log_error "Setup blocked: fix ERRORS found before rerunning."
            ;;
        *)
            log_error "Prerequisite checks failed."
            ;;
    esac

    if [ ${#PREREQ_SOLUTIONS[@]} -gt 0 ]; then
        echo -e "${CYAN}Suggested fixes:${NC}"
        for solution in "${PREREQ_SOLUTIONS[@]}"; do
            echo -e "  ${CYAN}•${NC} ${solution}"
        done
    else
        echo "Fix the errors above, then rerun the command."
    fi
}

# 重置 prereqs 追踪器
_prereqs_reset() {
    PREREQ_ERRORS=()
    PREREQ_WARNINGS=()
    PREREQ_SOLUTIONS=()
}

# 检查 Python3 版本是否 >= 3.12 (返回 0=通过, 1=不通过, 静默不打印)
# 用法: check_python3_version && echo "ok" || echo "fail"
check_python3_version() {
    if ! check_command python3; then
        return 1
    fi
    local py_version=$(python3 --version 2>&1 | awk '{print $2}')
    local py_major=$(echo "$py_version" | cut -d'.' -f1)
    local py_minor=$(echo "$py_version" | cut -d'.' -f2)
    [ "$py_major" -ge 3 ] && [ "$py_minor" -ge 12 ]
}

# 检查端口是否可用 (未被占用返回 0, 已被占用返回 1)
# 用法: check_port_available 8888 && echo "available" || echo "in use"
check_port_available() {
    local port="$1"
    if port_is_listening "$port"; then
        return 1
    else
        return 0
    fi
}

# 检查指定服务列表的前置依赖
# 逐个调用 <svc>_prereqs 函数 (若存在)，汇总结果
# 返回 0=全部通过, 1=有硬错误
# 用法: check_prereqs_for_services backend frontend bcs
check_prereqs_for_services() {
    local services=("$@")
    local has_error=false

    if [ ${#services[@]} -eq 0 ]; then
        log_warn "No services specified for prerequisite check"
        return 0
    fi

    _prereqs_reset

    echo ""
    echo "Checking prerequisites for: ${services[*]}"
    echo "==========================================="
    echo ""

    for svc in "${services[@]}"; do
        if type -t "${svc}_prereqs" &>/dev/null; then
            "${svc}_prereqs" || has_error=true
            echo ""
        else
            log_info "No prerequisites defined for ${svc} — skipping"
            echo ""
        fi
    done

    # 汇总
    echo "==========================================="
    if [ ${#PREREQ_ERRORS[@]} -gt 0 ]; then
        echo -e "${RED}ERRORS found:${NC}"
        for err in "${PREREQ_ERRORS[@]}"; do
            echo -e "  ${RED}•${NC} $err"
        done
        echo ""
    fi
    if [ ${#PREREQ_WARNINGS[@]} -gt 0 ]; then
        echo -e "${YELLOW}WARNINGS found:${NC}"
        for warn in "${PREREQ_WARNINGS[@]}"; do
            echo -e "  ${YELLOW}•${NC} $warn"
        done
        echo ""
    fi
    if [ "$has_error" = false ]; then
        if [ ${#PREREQ_WARNINGS[@]} -gt 0 ]; then
            log_info "All blocking prerequisite checks passed; warnings above may still need attention."
        else
            log_info "All prerequisite checks passed!"
        fi
    else
        print_prereq_failure_guidance
    fi
    echo ""

    if [ "$has_error" = true ]; then
        return 1
    fi
    return 0
}

# ============ Protobuf 检查与安装 ============

# 检查 protobuf 是否已安装
check_protobuf_installed() {
    if command -v protoc &> /dev/null; then
        return 0
    else
        return 1
    fi
}

# 获取 protobuf 版本
get_protobuf_version() {
    if check_protobuf_installed; then
        protoc --version 2>&1 | head -1
    else
        echo ""
    fi
}

# 在 macOS 上安装 protobuf
install_protobuf_macos() {
    log_info "Installing protobuf on macOS..."

    if command -v brew &> /dev/null; then
        brew install protobuf
        return $?
    else
        log_error "Homebrew not found. Please install Homebrew first: https://brew.sh"
        return 1
    fi
}

# 在 Linux 上安装 protobuf
install_protobuf_linux() {
    log_info "Installing protobuf on Linux..."

    # 检测包管理器并安装
    if command -v apt-get &> /dev/null; then
        # Debian/Ubuntu
        sudo apt-get update && sudo apt-get install -y protobuf-compiler
        return $?
    elif command -v yum &> /dev/null; then
        # CentOS/RHEL/Fedora
        sudo yum install -y protobuf-compiler
        return $?
    elif command -v dnf &> /dev/null; then
        # Fedora
        sudo dnf install -y protobuf-compiler
        return $?
    elif command -v pacman &> /dev/null; then
        # Arch Linux
        sudo pacman -S protobuf
        return $?
    else
        log_error "Unsupported Linux distribution. Please install protobuf manually."
        log_info "Visit: https://grpc.io/docs/protoc-installation/"
        return 1
    fi
}

# 安装 protobuf
install_protobuf() {
    log_info "Installing protobuf..."

    local os=$(uname -s)

    case "$os" in
        Darwin)
            install_protobuf_macos
            ;;
        Linux)
            install_protobuf_linux
            ;;
        *)
            log_error "Unsupported operating system: $os"
            log_info "Please install protobuf manually: https://grpc.io/docs/protoc-installation/"
            return 1
            ;;
    esac
}

# 设置 protobuf（检查并安装）
setup_protobuf() {
    log_info "Checking protobuf installation..."

    if check_protobuf_installed; then
        log_info "protobuf found: $(get_protobuf_version)"
        return 0
    fi

    log_warn "protobuf not found. It is required for building BCS (Rust). Installing..."

    if install_protobuf; then
        log_info "protobuf installed successfully: $(get_protobuf_version)"
        return 0
    else
        log_error "Failed to install protobuf"
        return 1
    fi
}

# ============ UV 包管理器 ============

# 检查 uv 是否安装 (静默检查，不打印日志)
check_uv_installed() {
    if command -v uv &> /dev/null; then
        return 0
    else
        return 1
    fi
}

# 自动安装 uv
auto_install_uv() {
    log_warn "uv not found. Attempting to install..."

    if command -v pip3 &> /dev/null; then
        log_info "Installing uv via pip3..."
        pip3 install uv -i "${PYPI_INDEX_URL}" && return 0
    elif command -v pip &> /dev/null; then
        log_info "Installing uv via pip..."
        pip install uv -i "${PYPI_INDEX_URL}" && return 0
    fi

    log_info "Attempting to install uv via official installer..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # 检查是否安装成功
    if command -v uv &> /dev/null; then
        log_info "uv installed successfully"
        return 0
    elif [ -f "$HOME/.local/bin/uv" ]; then
        export PATH="$HOME/.local/bin:$PATH"
        log_info "uv installed to ~/.local/bin/uv"
        return 0
    fi

    log_error "Failed to auto-install uv. Please install manually:"
    log_error "  pip3 install uv -i ${PYPI_INDEX_URL}"
    log_error "  or: curl -LsSf https://astral.sh/uv/install.sh | sh"
    return 1
}

# ============ OpenClaw 检查 ============

# 检查 openclaw 命令是否存在
check_openclaw_installed() {
    if command -v openclaw &> /dev/null; then
        return 0
    else
        return 1
    fi
}

extract_version_from_text() {
    printf '%s\n' "$1" \
        | grep -Eo '[0-9]+([.][0-9]+){1,2}' \
        | head -n 1 \
        | awk -F. '{for(i=1;i<=NF;i++) printf "%s%s", $i+0, (i<NF?".":"\n")}' \
        || true
}

version_at_least() {
    local current
    local minimum
    current="$(extract_version_from_text "$1")"
    minimum="$(extract_version_from_text "$2")"
    [ -n "$current" ] || return 2
    [ -n "$minimum" ] || return 2

    awk -v current="$current" -v minimum="$minimum" '
        BEGIN {
            split(current, c, ".")
            split(minimum, m, ".")
            for (i = 1; i <= 3; i++) {
                cv = (c[i] == "" ? 0 : c[i] + 0)
                mv = (m[i] == "" ? 0 : m[i] + 0)
                if (cv > mv) exit 0
                if (cv < mv) exit 1
            }
            exit 0
        }
    '
}

# 获取 openclaw 版本号（去掉每段前导零，规范化比较）
get_openclaw_version() {
    if check_openclaw_installed; then
        extract_version_from_text "$(openclaw --version 2>/dev/null | head -1)"
    fi
}

# Safely report or initialize the OpenClaw config.
#
# Default behavior is read-only: keep the user's existing ~/.openclaw/openclaw.json
# unchanged, because it may contain model providers and API keys. To intentionally
# initialize or overwrite it from scripts/openclaw.json, set:
#   OPENCLAW_CONFIG_OVERWRITE=1
copy_openclaw_config() {
    local force=false
    if [ "${1:-}" = "--force" ]; then
        force=true
    fi

    if [ -f "${OPENCLAW_CONFIG_FILE}" ]; then
        if [ "$force" = true ] && [ "${OPENCLAW_CONFIG_OVERWRITE:-0}" = "1" ]; then
            log_warn "OPENCLAW_CONFIG_OVERWRITE=1; replacing OpenClaw config: ${OPENCLAW_CONFIG_FILE}"
        else
            log_info "Reusing existing OpenClaw config: ${OPENCLAW_CONFIG_FILE}"
            log_info "Keeping local OpenClaw model settings unchanged."
            if [ "$force" = true ]; then
                log_info "Not overwriting. Set OPENCLAW_CONFIG_OVERWRITE=1 only if you intentionally want to replace it."
            fi
            return 0
        fi
    elif [ "${OPENCLAW_CONFIG_OVERWRITE:-0}" != "1" ]; then
        log_info "No OpenClaw config found at ${OPENCLAW_CONFIG_FILE}; not creating one automatically."
        log_info "OpenClaw can create its own config on first run, or set OPENCLAW_CONFIG_OVERWRITE=1 to initialize from ${OPENCLAW_CONFIG_TEMPLATE}."
        return 0
    else
        log_info "OPENCLAW_CONFIG_OVERWRITE=1; initializing OpenClaw config: ${OPENCLAW_CONFIG_FILE}"
    fi

    # 检查模板是否存在
    if [ ! -f "${OPENCLAW_CONFIG_TEMPLATE}" ]; then
        log_warn "openclaw config template not found at ${OPENCLAW_CONFIG_TEMPLATE}, skipping config copy"
        return 0
    fi

    # 创建配置目录
    mkdir -p "${OPENCLAW_CONFIG_DIR}"

    cp "${OPENCLAW_CONFIG_TEMPLATE}" "${OPENCLAW_CONFIG_FILE}"
    log_info "Copied openclaw config to ${OPENCLAW_CONFIG_FILE}"
}

# ============ Rust/Cargo 检查 ============

# 检查 Rust/Cargo 是否安装
check_rust_installed() {
    if command -v cargo &> /dev/null && command -v rustc &> /dev/null; then
        return 0
    else
        return 1
    fi
}

# ============ Hermes 检查 ============

# 检查 hermes 命令是否存在
check_hermes_installed() {
    if command -v hermes &> /dev/null; then
        return 0
    else
        return 1
    fi
}

# 获取 hermes 版本号
get_hermes_version() {
    if check_hermes_installed; then
        hermes --version 2>&1 | head -1
    else
        echo ""
    fi
}

# ============ Relay 检查 ============

# 检查 relay 仓库存在
check_relay_installed() {
    if [ -d "${RELAY_DIR}" ] && [ -f "${RELAY_DIR}/package.json" ]; then
        return 0
    else
        return 1
    fi
}

# ============ BCS 检查 ============

# Resolve the bcs server binary path. Honors BCS_BIN override (set by
# --with-bcs-coverage, which builds bcs into target/cov-e2e/.../debug);
# falls back to target/debug/bcs (matches start_bcs_binary's BCS_BIN usage).
bcs_bin_path() {
    echo "${BCS_BIN:-${BCS_DIR}/target/debug/bcs}"
}

# 检查 BCS binary 是否存在
check_bcs_binary() {
    local bcs_bin; bcs_bin="$(bcs_bin_path)"
    if [ -f "$bcs_bin" ] && [ -x "$bcs_bin" ]; then
        return 0
    else
        return 1
    fi
}

# Resolve the bcs-cli binary path. Honors BCS_CLI_BIN override (set by
# --with-bcs-coverage, which builds bcs-cli into target/cov-e2e/.../debug
# rather than the default target/debug); falls back to target/debug/bcs-cli.
bcs_cli_path() {
    echo "${BCS_CLI_BIN:-${BCS_DIR}/target/debug/bcs-cli}"
}

# 检查 bcs-cli binary 是否存在
check_bcs_cli_binary() {
    local bcs_cli; bcs_cli="$(bcs_cli_path)"
    if [ -f "$bcs_cli" ] && [ -x "$bcs_cli" ]; then
        return 0
    else
        return 1
    fi
}

# 检查 bcs-admin binary 是否存在
check_bcs_admin_binary() {
    local bcs_admin="${BCS_DIR}/target/debug/bcs-admin"
    if [ -f "$bcs_admin" ] && [ -x "$bcs_admin" ]; then
        return 0
    else
        return 1
    fi
}

# BCS 健康检查
bcs_health_ready() {
    curl --noproxy '*' --connect-timeout 1 --max-time 2 -s "http://127.0.0.1:${BCS_PORT}/health" > /dev/null 2>&1
}

# BCS 自动 onboard 是否启用
bcs_auto_onboard_enabled() {
    case "$BCS_AUTO_ONBOARD" in
        auto|"")
            return 0
            ;;
        1|true|yes|on)
            return 0
            ;;
        0|false|no|off)
            return 1
            ;;
        *)
            log_error "Invalid BCS_AUTO_ONBOARD: ${BCS_AUTO_ONBOARD}. Valid values: auto, 1, 0"
            exit 1
            ;;
    esac
}

# 解析 BCS_SERVER_ENV
resolve_bcs_server_env() {
    if [ -z "$BCS_SERVER_ENV" ]; then
        if [ "$LOCAL_MODE" = true ]; then
            BCS_SERVER_ENV="local"
        else
            BCS_SERVER_ENV="dev"
        fi
    fi

    case "$BCS_SERVER_ENV" in
        local|dev)
            ;;
        *)
            log_error "Invalid BCS_SERVER_ENV: ${BCS_SERVER_ENV}. Valid values: local, dev"
            exit 1
            ;;
    esac
}

# ============ BCSFuse 检查 ============

# 检查 bcsfuse 目录是否存在
check_bcsfuse_directory() {
    if [ -d "${BCSFUSE_DIR}" ]; then
        return 0
    else
        log_error "bcsfuse directory not found at ${BCSFUSE_DIR}"
        return 1
    fi
}

# ============ Local Mode 信息 ============

# 显示本地模式信息
show_local_mode_info() {
    echo ""
    log_warn "========================================="
    log_warn "LOCAL MODE ENABLED"
    log_warn "========================================="
    log_warn "Using:"
    log_warn "  - Services: BAAS + Backend + BCS + 5 local bots + demo bot + frontend"
    log_warn "  - Auth: BCS local mock identity"
    log_warn "  - BCS: local config (no external MySQL/Redis services)"
    log_warn "  - OpenClaw configs: ${LOCAL_OPENCLAW_DIR}"
    log_warn "  - Aidesktop data: ${LOCAL_AIDESKTOP_DIR}"
    log_warn "========================================="
    echo ""
}

# ============ BCN plugin source mode ============
# openclaw-channel-bcn can be provided two ways, selected by BCN_PLUGIN_SOURCE:
#   source (default) - build from the in-repo src/plugin tree
#   npm              - install @avernet-plugin/openclaw-channel-bcn via openclaw
BCN_PLUGIN_NPM_PACKAGE="@avernet-plugin/openclaw-channel-bcn"

bcn_plugin_mode() {
    local mode="${BCN_PLUGIN_SOURCE:-source}"
    case "$mode" in
        source|npm)
            printf '%s\n' "$mode"
            ;;
        *)
            log_error "Invalid BCN_PLUGIN_SOURCE: '${mode}'. Valid values: source, npm"
            return 1
            ;;
    esac
}

bcn_plugin_version() {
    printf '%s\n' "${BCN_PLUGIN_VERSION:-latest}"
}

bcn_plugin_npm_spec() {
    printf '%s@%s\n' "${BCN_PLUGIN_NPM_PACKAGE}" "$(bcn_plugin_version)"
}

# Resolve where an npm-installed BCN plugin lives. The native installer targets
# the global extensions root (~/.openclaw/extensions); a custom
# OPENCLAW_EXTENSIONS_ROOT (standalone) is checked first.
bcn_plugin_resolve_npm_dir() {
    local cand
    for cand in \
        "${OPENCLAW_EXTENSIONS_ROOT:-${HOME}/.openclaw/extensions}/openclaw-channel-bcn" \
        "${HOME}/.openclaw/extensions/openclaw-channel-bcn"; do
        if [ -d "$cand" ] && [ ! -L "$cand" ]; then
            printf '%s\n' "$cand"
            return 0
        fi
    done
    return 1
}

# Install the published BCN plugin via OpenClaw's native installer and echo the
# resolved install directory. Never falls back to a source build.
bcn_plugin_ensure_npm() {
    if ! check_command openclaw; then
        log_error "openclaw not found; required for BCN_PLUGIN_SOURCE=npm. Run: ./scripts/singlebox.sh install-tools"
        return 1
    fi
    local spec
    spec="$(bcn_plugin_npm_spec)"
    log_info "Installing BCN plugin from npm: ${spec}" >&2
    if ! openclaw plugins install "npm:${spec}" --force --pin >&2; then
        log_error "Failed to install BCN plugin from npm: npm:${spec}"
        return 1
    fi
    local dir
    if ! dir="$(bcn_plugin_resolve_npm_dir)"; then
        log_error "BCN plugin installed but its directory was not found under the extensions root(s)"
        return 1
    fi
    printf '%s\n' "$dir"
}

# Ensure ${link} is a symlink to ${target}. replace=1 permits replacing a link
# that points elsewhere; a non-symlink at ${link} is kept unless replace=1
# (then it is an error).
ensure_bcn_symlink() {
    local target="$1"
    local link="$2"
    local replace="${3:-0}"

    mkdir -p "$(dirname "$link")"

    if [ -L "$link" ]; then
        local current_target
        current_target="$(readlink "$link")"
        if [ "$current_target" = "$target" ]; then
            log_info "BCN plugin symlink already correct: ${link}"
        elif [ "$replace" = "1" ]; then
            rm -f "$link"
            ln -s "$target" "$link"
            log_info "BCN plugin relinked: ${link} -> ${target}"
        else
            log_info "BCN plugin symlink points elsewhere, keeping: ${link} -> ${current_target}"
        fi
        return 0
    fi

    if [ -e "$link" ]; then
        if [ "$replace" = "1" ]; then
            log_error "BCN plugin link path exists and is not a symlink: ${link}"
            return 1
        fi
        log_info "BCN plugin path already exists, keeping: ${link}"
        return 0
    fi

    ln -s "$target" "$link"
    log_info "BCN plugin linked: ${link} -> ${target}"
}
