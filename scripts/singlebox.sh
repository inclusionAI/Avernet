#!/usr/bin/env bash
set -e

# ============ singlebox.sh — Local development environment entry point ============
#
# Usage:
#   ./scripts/singlebox.sh                              # Current local default (setup current all group + start current all group)
#   ./scripts/singlebox.sh install-tools                # Install/upgrade dev tools (toolchain)
#   ./scripts/singlebox.sh setup [service|group|all]    # Prepare artifacts/config/links; rebuild stale binaries
#   ./scripts/singlebox.sh start [service|group|all]    # Start target (default: current all group)
#   ./scripts/singlebox.sh stop [service|group|all]     # Stop target (default: current all group)
#   ./scripts/singlebox.sh restart [service|group|all]  # Restart target (default: current all group)
#   ./scripts/singlebox.sh clean [service|group|all]    # Clean local runtime data for target
#   ./scripts/singlebox.sh status [group|service|all]   # Show status (default: current all group)
#   ./scripts/singlebox.sh check [service|group|all]    # Check prerequisites (default: current all group)
#   ./scripts/singlebox.sh --standalone [command]       # Run isolated standalone BCS + OpenClaw stack
#
# Compatibility:
#
# Groups:
#   all             Current open-source default (BCS + 5 bots + Frontend)
#   bcs_bots        BCS + 5 local OpenClaw bots
#   bcs_frontend    BCS + Frontend (E2E)
#

# ============ 常量配置 ============
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

load_repo_env_file() {
    local env_file="$1"
    [ -f "$env_file" ] || return 0

    set -a
    # shellcheck source=/dev/null
    . "$env_file"
    set +a
}

# .env.example is a template only. Load local overrides without overriding
# explicit environment variables through repository defaults.
load_repo_env_file "${PROJECT_ROOT}/.env.local"

DEP_DIR="${SCRIPT_DIR}/.dependencies"
LOG_DIR="${DEP_DIR}/logs"
RUNTIME_DATA_DIR="${DEP_DIR}/data"

# Monorepo 目录结构
BACKEND_DIR="${PROJECT_ROOT}/src/backend"
FRONTEND_DIR="${PROJECT_ROOT}/src/frontend"
ENGINE_DIR="${PROJECT_ROOT}/src/engine"
BCS_DIR="${PROJECT_ROOT}/src/bcs"
BCSFUSE_DIR="${PROJECT_ROOT}/src/bcsfuse"
RELAY_DIR="${RELAY_DIR:-${PROJECT_ROOT}/../teamclaw-aicoding-relay}"
BAAS_DIR="${PROJECT_ROOT}/src/baas"

# 默认版本
DEFAULT_OPENCLAW_VERSION=">=2026.3.28"
DEFAULT_BCS_PORT="21000"
DEFAULT_RELAY_PORT="18900"
DEFAULT_HERMES_PORT="18765"
DEFAULT_HERMES_PORT_START="18700"
DEFAULT_HERMES_PORT_END="18799"
DEFAULT_BCSFUSE_PORT="8765"
DEFAULT_FRONTEND_PORT="8000"
OPENCLAW_VERSION="${OPENCLAW_VERSION:-${DEFAULT_OPENCLAW_VERSION}}"
BCS_PORT="${BCS_PORT:-${DEFAULT_BCS_PORT}}"
RELAY_PORT="${RELAY_PORT:-${DEFAULT_RELAY_PORT}}"
BCS_SERVER_ENV="${BCS_SERVER_ENV:-}"
BCS_AUTO_ONBOARD="${BCS_AUTO_ONBOARD:-auto}"
HERMES_PORT="${HERMES_PORT:-${DEFAULT_HERMES_PORT}}"
HERMES_PORT_START="${HERMES_PORT_START:-${DEFAULT_HERMES_PORT_START}}"
HERMES_PORT_END="${HERMES_PORT_END:-${DEFAULT_HERMES_PORT_END}}"
BCSFUSE_PORT="${BCSFUSE_PORT:-${DEFAULT_BCSFUSE_PORT}}"
FRONTEND_PORT="${FRONTEND_PORT:-${DEFAULT_FRONTEND_PORT}}"
CHAT_ENGINE="${CHAT_ENGINE:-openclaw}"
BOTS_PROFILE_DIR="${BOTS_PROFILE_DIR:-}"
BCN_PLUGIN_SOURCE="${BCN_PLUGIN_SOURCE:-source}"
BCN_PLUGIN_VERSION="${BCN_PLUGIN_VERSION:-latest}"

# openclaw 配置目录和模板路径
OPENCLAW_CONFIG_DIR="${HOME}/.openclaw"
OPENCLAW_CONFIG_FILE="${OPENCLAW_CONFIG_DIR}/openclaw.json"
OPENCLAW_CONFIG_TEMPLATE="${SCRIPT_DIR}/openclaw.json"

# ============ 镜像源 / Mirror switch ============
# USE_CN_MIRROR 是统一开关：空 = 公网上游，=1 = 国内公开镜像。
# 与 Dockerfile.ocb 的 ARG USE_CN_MIRROR 同名同义。
USE_CN_MIRROR="${USE_CN_MIRROR:-}"

# Python PyPI 索引（公网默认；USE_CN_MIRROR=1 时自动切到 TUNA）
PYPI_INDEX_URL="${PYPI_INDEX_URL:-https://pypi.org/simple}"
# npm registry（公网默认；USE_CN_MIRROR=1 时自动切到 npmmirror）
NPM_REGISTRY_URL="${NPM_REGISTRY_URL:-https://registry.npmjs.org}"

apply_cn_mirror_overrides() {
    [ "${USE_CN_MIRROR}" = "1" ] || return 0

    # 只有用户没显式覆盖时才替换（允许 PYPI_INDEX_URL=... USE_CN_MIRROR=1 组合）
    if [ "${PYPI_INDEX_URL}" = "https://pypi.org/simple" ]; then
        PYPI_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
    fi
    if [ "${NPM_REGISTRY_URL}" = "https://registry.npmjs.org" ]; then
        NPM_REGISTRY_URL="https://registry.npmmirror.com"
    fi

    # nvm 下 node 二进制走 npmmirror
    export NVM_NODEJS_ORG_MIRROR="${NVM_NODEJS_ORG_MIRROR:-https://npmmirror.com/mirrors/node/}"
    export NVM_NODEJS_MIRROR="${NVM_NODEJS_MIRROR:-https://npmmirror.com/mirrors/node/}"

    # rustup 走 USTC（与 Dockerfile.ocb 一致）
    export RUSTUP_DIST_SERVER="${RUSTUP_DIST_SERVER:-https://mirrors.ustc.edu.cn/rust-static}"
    export RUSTUP_UPDATE_ROOT="${RUSTUP_UPDATE_ROOT:-https://mirrors.ustc.edu.cn/rust-static/rustup}"

    # corepack 拉 pnpm/yarn tarball 也走 npmmirror
    export COREPACK_NPM_REGISTRY="${COREPACK_NPM_REGISTRY:-https://registry.npmmirror.com}"
}

# 引擎类型状态文件
ENGINE_STATE_FILE="${LOG_DIR}/.engine_type"

# ============ 运行模式 ============
LOCAL_MODE=true
with_bcs_coverage=0
STANDALONE_MODE=false

# Local Mode directories
LOCAL_BOTS_DIR="${PROJECT_ROOT}/test-bots"
LOCAL_OPENCLAW_DIR="${LOCAL_BOTS_DIR}/openclaw"
LOCAL_AIDESKTOP_DIR="${LOCAL_BOTS_DIR}/aidesktop"
STANDALONE_OPENCLAW_ROOT="${STANDALONE_OPENCLAW_ROOT:-${PROJECT_ROOT}/.standalone-openclaw}"
STANDALONE_RUNTIME_DIR="${STANDALONE_RUNTIME_DIR:-${DEP_DIR}/standalone}"

# ============ 准备目录 ============
mkdir -p "${RUNTIME_DATA_DIR}"

# ============ 加载模块 ============
# 顺序: utils → toolchain → service modules → group modules
source "${SCRIPT_DIR}/utils.sh"
apply_cn_mirror_overrides
source "${SCRIPT_DIR}/toolchain.sh"

# Service modules
source "${SCRIPT_DIR}/modules/frontend.sh"
source "${SCRIPT_DIR}/modules/bcs.sh"
source "${SCRIPT_DIR}/modules/bots.sh"
source "${SCRIPT_DIR}/modules/bcs_bots.sh"

# Group modules (must be after service modules they compose)
source "${SCRIPT_DIR}/modules/all.sh"
source "${SCRIPT_DIR}/modules/bcs_frontend.sh"

# ============ Git hooks ============
ensure_git_hooks_installed() {
    if [ "${OCB_SKIP_GIT_HOOKS:-0}" = "1" ]; then
        return 0
    fi

    local installer="${PROJECT_ROOT}/scripts/install_git_hooks.sh"
    if [ ! -f "$installer" ]; then
        log_warn "Git hook installer not found: ${installer}"
        return 0
    fi

    "$installer" --quiet || log_warn "Failed to install git hooks; run ./scripts/install_git_hooks.sh manually"
}

# ============ 服务组展开 ============

# 将组名或服务名展开为服务列表
# all → START_ORDER; 其他 → 原样返回
resolve_services() {
    local target="$1"
    case "$target" in
        all)
            echo "${START_ORDER[*]}"
            ;;
        bcs_frontend)
            if [ "$LOCAL_MODE" = true ]; then
                echo "bcs_bots frontend"
            else
                echo "bcs frontend"
            fi
            ;;
        bcs_bots)
            echo "bcs bots"
            ;;
        *)
            echo "$target"
            ;;
    esac
}

# ============ 服务启动顺序 ============
# NOTE: Service ordering is now defined in group modules:
#   - all.sh:   SETUP_ORDER / START_ORDER  (for "all" group)
# Do NOT add order arrays here — always edit the group module.

# ============ 约定式分发 ============

# 执行服务的 setup 动作
setup_service() {
    local svc="$1"
    if [ -z "$svc" ]; then
        log_error "Service name required for setup command"
        echo "Run '$0 --help' for available services"
        exit 1
    fi
    if type -t "${svc}_setup" &>/dev/null; then
        "${svc}_setup"
    else
        log_warn "$svc has no setup — skipping"
    fi
}

# 执行服务的 start 动作
start_service() {
    local svc="$1"
    if [ -z "$svc" ]; then
        log_error "Service name required for start command"
        echo "Run '$0 --help' for available services"
        exit 1
    fi
    # 自动检查该服务的前置依赖
    if type -t "${svc}_prereqs" &>/dev/null; then
        if ! "${svc}_prereqs"; then
            local check_cmd
            check_cmd="$(singlebox_cmd check "$svc")"
            if [ "$svc" = "bots" ] && [ -n "${BOTS_PROFILE_DIR:-}" ]; then
                check_cmd="${check_cmd} --profile-dir ${BOTS_PROFILE_DIR}"
            fi
            log_error "Prerequisites not met for ${svc}. Fix errors above or run: ${check_cmd}"
            return 1
        fi
    fi
    if type -t "${svc}_start" &>/dev/null; then
        "${svc}_start"
    else
        log_error "Unknown service or no start: $svc"
        exit 1
    fi
}

# 执行服务的 stop 动作
stop_service() {
    local svc="$1"
    if [ -z "$svc" ]; then
        log_error "Service name required for stop command"
        echo "Run '$0 --help' for available services"
        exit 1
    fi
    if type -t "${svc}_stop" &>/dev/null; then
        "${svc}_stop"
    else
        log_error "Unknown service or no stop: $svc"
        exit 1
    fi
}

# 执行服务的 restart 动作 (默认 stop + sleep + start, 模块可覆盖)
restart_service() {
    local svc="$1"
    if [ -z "$svc" ]; then
        log_error "Service name required for restart command"
        echo "Run '$0 --help' for available services"
        exit 1
    fi
    if type -t "${svc}_restart" &>/dev/null; then
        "${svc}_restart"
    elif type -t "${svc}_stop" &>/dev/null && type -t "${svc}_start" &>/dev/null; then
        "${svc}_stop"
        sleep 2
        "${svc}_start"
    else
        log_error "Unknown service: $svc"
        exit 1
    fi
}

# 执行服务的 clean 动作
clean_service() {
    local svc="$1"
    if [ -z "$svc" ]; then
        log_error "Service name required for clean command"
        echo "Run '$0 --help' for available services"
        exit 1
    fi
    if type -t "${svc}_clean" &>/dev/null; then
        "${svc}_clean"
    else
        log_warn "$svc has no clean — skipping"
    fi
}

# 显示所有服务状态
show_status() {
    local target="${1:-all}"

    # Delegate to group or service status function if it exists
    if type -t "${target}_status" &>/dev/null; then
        "${target}_status"
        return
    fi

    # Fallback: show current all group status via group module
    all_status
}

validate_standalone_root() {
    local root="$1"
    case "$root" in
        ""|"/"|"$HOME"|"$HOME/.openclaw"|"$HOME/.openclaw"/*|"${PROJECT_ROOT}"|"${SCRIPT_DIR}"|"${BCS_DIR}")
            log_error "Unsafe standalone OpenClaw root: ${root}"
            exit 1
            ;;
    esac
}

apply_singlebox_mode_defaults() {
    if [ "$STANDALONE_MODE" != true ]; then
        return 0
    fi

    LOCAL_MODE=true
    validate_standalone_root "${STANDALONE_OPENCLAW_ROOT}"

    BCS_DATA_DIR="${STANDALONE_RUNTIME_DIR}/bcs_data"
    BCS_RUNTIME_CONFIG_DIR="${STANDALONE_RUNTIME_DIR}/bcs-config"
    BCS_BOTS_STACK_PID_FILE="${STANDALONE_RUNTIME_DIR}/bcs_bots_stack.pid"
    BCS_BOTS_STACK_LOG="${STANDALONE_RUNTIME_DIR}/bcs_bots_stack.log"
    BCS_BOT_PORTS_FILE="${STANDALONE_RUNTIME_DIR}/bcs_bot_ports.env"

    OPENCLAW_PROFILE_ROOT="${STANDALONE_OPENCLAW_ROOT}/profiles"
    OPENCLAW_PROFILE_PREFIX=""
    OPENCLAW_WORKSPACE_ROOT="${STANDALONE_OPENCLAW_ROOT}/workspaces"
    OPENCLAW_WORKSPACE_LAYOUT="profile"
    OPENCLAW_EXTENSIONS_ROOT="${STANDALONE_OPENCLAW_ROOT}/extensions"
    OPENCLAW_EXTENSIONS_REPLACE_LINKS=1
    OPENCLAW_LOG_ROOT="${STANDALONE_OPENCLAW_ROOT}/logs"

    export BCS_DATA_DIR BCS_RUNTIME_CONFIG_DIR BCS_BOTS_STACK_PID_FILE BCS_BOTS_STACK_LOG BCS_BOT_PORTS_FILE
    export OPENCLAW_PROFILE_ROOT OPENCLAW_PROFILE_PREFIX OPENCLAW_WORKSPACE_ROOT OPENCLAW_WORKSPACE_LAYOUT
    export OPENCLAW_EXTENSIONS_ROOT OPENCLAW_EXTENSIONS_REPLACE_LINKS OPENCLAW_LOG_ROOT
}

show_standalone_mode_info() {
    echo ""
    log_warn "========================================="
    log_warn "STANDALONE MODE ENABLED"
    log_warn "========================================="
    log_warn "Using isolated standalone BCS + OpenClaw paths:"
    log_warn "  - OpenClaw root: ${STANDALONE_OPENCLAW_ROOT}"
    log_warn "  - Bot profiles: ${OPENCLAW_PROFILE_ROOT}/<bot-profile>"
    log_warn "  - Bot workspaces: ${OPENCLAW_WORKSPACE_ROOT}/<bot-profile>"
    log_warn "  - Plugin link: ${OPENCLAW_EXTENSIONS_ROOT}/openclaw-channel-bcn"
    log_warn "  - BCS runtime: ${STANDALONE_RUNTIME_DIR}"
    log_warn "========================================="
    echo ""
}

# 显示帮助信息
show_help() {
    echo "Usage: $0 [OPTIONS] [COMMAND]"
    echo ""
    echo "Local development environment manager."
    echo ""
    echo "Modes:"
    echo "  --local, -l     Use local BCS + frontend defaults (default)"
    echo "                  - Services: BCS + 5 local bots + frontend; no backend service in this profile"
    echo "                  - Auth: BCS local mock identity"
    echo "                  - BCS: SERVER_ENV=local, no external MySQL/Redis services"
    echo ""
    echo "  --standalone, -s Use isolated standalone BCS + OpenClaw paths"
    echo "                  - Starts the same BCS 5bot stack + frontend flow"
    echo "                  - Writes BCS runtime under scripts/.dependencies/standalone"
    echo "                  - Writes OpenClaw profiles, workspaces, and plugin link under .standalone-openclaw"
    echo ""
    echo "  (default)       Same as --local"
    echo ""
    echo ""
    echo "Commands:"
    echo "  install-tools                  Install/upgrade dev tools (node, npm, uv, openclaw, ...)"
    echo "  setup [service|group|all]      Prepare artifacts/config/links; rebuild stale binaries"
    echo "  start [service|group|all]      Start target (default: current all group)"
    echo "  stop [service|group|all]       Stop target (default: current all group)"
    echo "  restart [service|group|all]    Restart target (default: current all group)"
    echo "  clean [service|group|all]      Clean local runtime data for target"
    echo "  status [group|service|all]     Show status (default: current all group)"
    echo "  check [service|group|all]      Check prerequisites (default: current all group)"
    echo ""
    echo "  (no arguments)                 Current local default: setup current all group + start current all group"
    echo ""
    echo "Options:"
    echo "  --local, -l                   Use local BCS + 5bot + frontend defaults"
    echo "  --standalone, -s              Use isolated standalone BCS + OpenClaw + frontend flow; forwards remaining args"
    echo "  --openclaw-version, -ov VERSION Specify openclaw npm version/range (default: ${DEFAULT_OPENCLAW_VERSION})"
    echo "  --bcs-port, -bp PORT          Specify BCS port (default: ${DEFAULT_BCS_PORT})"
    echo "  --frontend-port, -fp PORT     Specify frontend dev server port (default: ${DEFAULT_FRONTEND_PORT})"
    echo "  --bcs-env local|dev           BCS runtime env; default: local with --local, otherwise dev"
    echo "  --bcn-plugin-source source|npm  BCN plugin source: build from repo (source, default) or install"
    echo "                                  @avernet-plugin/openclaw-channel-bcn (npm). Env: BCN_PLUGIN_SOURCE,"
    echo "                                  BCN_PLUGIN_VERSION (npm mode, default latest)"
    echo "  --profile-dir DIR            Bot persona source dir for 'bots' target; requires DIR/bots.json"
    echo "  --bcs-auto-onboard            Legacy compatibility flag; use bcs_bots for BCS + bots"
    echo "  --no-bcs-auto-onboard         Legacy compatibility flag; use bcs for BCS-only"
    echo "  --with-bcs-coverage           Build instrumented bcs (target/cov-e2e) for e2e line coverage"
    echo "  -h, --help                    Show this help message"
    echo ""
    echo "Services:"
    # 从各模块收集帮助信息
    for svc in frontend bcs bots; do
        if type -t "${svc}_help" &>/dev/null; then
            echo "  $( "${svc}_help" )"
        fi
    done
    echo ""
    echo "Groups:"
    for grp in all bcs_bots bcs_frontend; do
        if type -t "${grp}_help" &>/dev/null; then
            echo "  $( "${grp}_help" )"
        fi
    done
    echo ""
    echo "Examples:"
    echo "  $0 install-tools               First time: install dev tools"
    echo "  $0                             Setup and start current all group (currently BCS + 5bots + frontend)"
    echo "  $0 setup all                   Setup current all group (currently BCS + 5bots + frontend)"
    echo "  $0 start all                   Start current all group (currently BCS + 5bots + frontend)"
    echo "  $0 restart bcs                 Restart only the BCS server"
    echo "  $0 restart bots                Restart only the 5 local bot gateways"
    echo "  $0 start bots --profile-dir scripts/8bots_micro_merchant_profile"
    echo "  $0 restart bcs_bots            Restart BCS + 5 local bot gateways"
    echo "  $0 clean bcs                   Clean only local BCS runtime data"
    echo "  $0 clean bots                  Clean only local bot profiles/workspaces"
    echo "  $0 check                       Check prerequisites"
    echo "  $0 --standalone check          Check the isolated standalone local stack"
    echo "  $0 --standalone                Setup and start local stack under .standalone-openclaw"
    echo "  $0 status                      Show service status"
    echo ""
}

# 编译插编 bcs 到 target/cov-e2e/llvm-cov-target/ 并 export BCS_BIN/LLVM_PROFILE_FILE。
# 由 --with-bcs-coverage 触发;缓存:已存在则 skip 编译。
#
# 路径布局:cargo llvm-cov report 从 <CARGO_TARGET_DIR>/llvm-cov-target/ 读 profraw
# 和对象文件。故 build 时 CARGO_TARGET_DIR 必须 = target/cov-e2e/llvm-cov-target,
# 使 bcs 落 .../llvm-cov-target/debug/bcs,profraw 也落 .../llvm-cov-target/*.profraw,
# 与对象同层,report 一搜就对。
prepare_bcs_coverage_bin() {
    local cov_root="$BCS_DIR/target/cov-e2e"
    local cov_dir="$cov_root/llvm-cov-target"
    local cov_bin="$cov_dir/debug/bcs"

    # 环境校验:cargo-llvm-cov + llvm-tools(rustup component list --installed
    # 输出带 target 后缀,如 llvm-tools-x86_64-apple-darwin,用 ^llvm-tools 匹配)
    if ! command -v cargo-llvm-cov >/dev/null 2>&1; then
        log_error "cargo-llvm-cov not installed. Run: cargo install cargo-llvm-cov --locked"
        exit 1
    fi
    if ! rustup component list --installed 2>/dev/null | grep -q '^llvm-tools'; then
        log_error "llvm-tools component not installed. Run: rustup component add llvm-tools"
        exit 1
    fi

    # Keep the profraw dir present even on a cache hit (it may have been pruned).
    mkdir -p "$cov_dir"

    if [[ ! -x "$cov_bin" ]] || [[ "${BCS_COVERAGE_FORCE_REBUILD:-0}" == "1" ]]; then
        if [[ "${BCS_COVERAGE_FORCE_REBUILD:-0}" == "1" ]]; then
            log_info "Force-rebuilding instrumented bcs (BCS_COVERAGE_FORCE_REBUILD=1)..."
        else
            log_info "Building instrumented bcs for coverage (target/cov-e2e/llvm-cov-target)..."
        fi
        (
            cd "$BCS_DIR"
            # Manually set RUSTFLAGS to instrument the cargo build. Don't use
            # `source <(cargo llvm-cov show-env)` + a bare `cargo build` —
            # cargo-llvm-cov 0.8.x's wrapper only injects instrumentation flags
            # for `cargo llvm-cov <subcommand>`, so a bare build yields a binary
            # with zeroed __llvm_profile symbols that writes no profraw.
            # -Clink-dead-code keeps LLVM's uncalled functions so line coverage
            # attribution has the full function table.
            # LLVM_PROFILE_FILE here redirects profraw emitted during the build
            # itself (build scripts / proc-macros run instrumented but without a
            # profile path, defaulting to default-*.profraw in each crate's
            # cwd). Pointing it at target/tmp keeps the source tree clean; those
            # build-time profiles are excluded from the report anyway (--package
            # bcs). Runtime profraw (the bcs binary) is controlled by the
            # LLVM_PROFILE_FILE set later for actual runs.
            export RUSTFLAGS="-Cinstrument-coverage -Clink-dead-code"
            export CARGO_INCREMENTAL=0
            export CARGO_PROFILE_DEV_DEBUG=line-tables-only
            export CARGO_TARGET_DIR="$cov_dir"
            local build_tmp="$BCS_DIR/target/tmp"
            mkdir -p "$build_tmp"
            export LLVM_PROFILE_FILE="$build_tmp/build-%m-%p.profraw"
            cargo build --package bcs --package bcs-cli
        ) || { log_error "coverage build failed"; exit 1; }
    else
        log_info "Reusing cached instrumented bcs: $cov_bin"
    fi

    export BCS_BIN="$cov_bin"
    export BCS_CLI_BIN="$cov_dir/debug/bcs-cli"
    export LLVM_PROFILE_FILE="$cov_dir/bcs-%m-%p.profraw"
}

# ============ 当前默认安装流程 (无参数调用) ============
setup_all_and_start() {
    # 加载之前保存的引擎类型
    load_engine_type

    if [[ "$with_bcs_coverage" -eq 1 ]]; then
        prepare_bcs_coverage_bin
    fi

    # 显示模式信息
    if [ "$STANDALONE_MODE" = true ]; then
        show_standalone_mode_info
    elif [ "$LOCAL_MODE" = true ]; then
        show_local_mode_info
    fi

    # 根据模式设置环境变量
    if [ "$LOCAL_MODE" = true ]; then
        export SERVER_ENV=dev
        export LOCAL_DEV_MODE=true
    else
        export DATABASE_MODE=mysql
        export SERVER_ENV=dev
    fi
    resolve_bcs_server_env

    # 创建必要的目录
    mkdir -p "${DEP_DIR}" "${LOG_DIR}"

    echo ""
    echo "======================================"
    echo "  Development Environment Setup"
    echo "======================================"
    echo ""

    # Setup current all group (via group module)
    log_info "Running setup current all group..."
    if ! all_setup; then
        log_error "Setup failed. Fix the errors above, then rerun ./scripts/singlebox.sh --local."
        return 1
    fi

    # Start current all group (via group module)
    log_info "Running start current all group..."
    if ! all_start; then
        log_error "Start failed. Fix the errors above, then rerun ./scripts/singlebox.sh --local."
        return 1
    fi

    echo ""
    log_info "Development environment is ready!"
    echo ""
    show_status
}

# ============ 主函数 ============
main() {
    # 加载之前保存的引擎类型
    load_engine_type

    # 解析命令行参数
    local command=""
    local services=()

    while [ $# -gt 0 ]; do
        case $1 in
            --dev|-d)
                LOCAL_MODE=false
                BCS_SERVER_ENV=dev
                shift
                ;;
            --local|-l)
                LOCAL_MODE=true
                shift
                ;;
            --standalone|-s)
                STANDALONE_MODE=true
                LOCAL_MODE=true
                shift
                ;;
            --openclaw-version|-ov)
                if [ -z "$2" ] || [ "$2" = -* ]; then
                    log_error "Version number required for $1"
                    show_help
                    exit 1
                fi
                OPENCLAW_VERSION="$2"
                shift 2
                ;;
            --bcs-port|-bp)
                if [ -z "$2" ] || [ "$2" = -* ]; then
                    log_error "Port number required for $1"
                    show_help
                    exit 1
                fi
                BCS_PORT="$2"
                shift 2
                ;;
            --frontend-port|-fp)
                if [ -z "$2" ] || [ "$2" = -* ]; then
                    log_error "Port number required for $1"
                    show_help
                    exit 1
                fi
                FRONTEND_PORT="$2"
                shift 2
                ;;
            --engine|-e)
                if [ -z "$2" ] || [ "$2" = -* ]; then
                    log_error "Engine type required for $1"
                    show_help
                    exit 1
                fi
                CHAT_ENGINE="$2"
                save_engine_type
                shift 2
                ;;
            --bcn-plugin-source)
                if [ -z "$2" ] || [ "$2" = -* ]; then
                    log_error "Mode required for $1 (source|npm)"
                    show_help
                    exit 1
                fi
                BCN_PLUGIN_SOURCE="$2"
                shift 2
                ;;
            --bcs-env)
                if [ -z "$2" ] || [ "$2" = -* ]; then
                    log_error "BCS environment required for $1"
                    show_help
                    exit 1
                fi
                BCS_SERVER_ENV="$2"
                shift 2
                ;;
            --profile-dir)
                if [ -z "$2" ] || [ "$2" = -* ]; then
                    log_error "Profile directory required for $1"
                    show_help
                    exit 1
                fi
                BOTS_PROFILE_DIR="$2"
                shift 2
                ;;
            --bcs-auto-onboard)
                BCS_AUTO_ONBOARD=1
                shift
                ;;
            --no-bcs-auto-onboard)
                BCS_AUTO_ONBOARD=0
                shift
                ;;
            --with-bcs-coverage)
                with_bcs_coverage=1
                shift
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            setup|start|stop|restart|clean)
                command="$1"
                shift
                # Collect all following service names
                while [ $# -gt 0 ] && [[ "$1" != -* ]]; do
                    services+=("$1")
                    shift
                done
                ;;
            install-tools)
                command="install-tools"
                shift
                ;;
            status|check)
                command="$1"
                shift
                # status and check can optionally take service names
                while [ $# -gt 0 ] && [[ "$1" != -* ]]; do
                    services+=("$1")
                    shift
                done
                ;;
            standalone)
                # Compatibility alias for the old command form. Prefer --standalone.
                STANDALONE_MODE=true
                LOCAL_MODE=true
                shift
                ;;
            frontend|bcs|bots|bcs_bots|bcs_frontend|all)
                # Legacy: service name without command defaults to start
                services+=("$1")
                if [ -z "$command" ]; then
                    command="start"
                fi
                shift
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done

    # 根据模式设置环境变量
    if [ "$LOCAL_MODE" = true ]; then
        export SERVER_ENV=dev
        export LOCAL_DEV_MODE=true
    else
        export DATABASE_MODE=mysql
        export SERVER_ENV=dev
    fi
    apply_singlebox_mode_defaults
    resolve_bcs_server_env

    # 创建必要的目录
    mkdir -p "${DEP_DIR}" "${LOG_DIR}"
    ensure_git_hooks_installed
    if [ "$STANDALONE_MODE" = true ]; then
        mkdir -p "${STANDALONE_RUNTIME_DIR}"
    fi

    # 处理命令
    # Default to current all group if no services specified
    if [ ${#services[@]} -eq 0 ]; then
        services=(all)
    fi
    if [ -n "${BOTS_PROFILE_DIR:-}" ]; then
        for svc in "${services[@]}"; do
            if [ "$svc" != "bots" ]; then
                log_error "--profile-dir only supports the bots target, for example: ./scripts/singlebox.sh $(singlebox_mode_option) start bots --profile-dir <dir>"
                exit 1
            fi
        done
    fi
    if [ "$STANDALONE_MODE" = true ]; then
        export SINGLEBOX_MODE=standalone
    else
        export SINGLEBOX_MODE=local
    fi
    local resolved_bcn_mode
    if ! resolved_bcn_mode="$(bcn_plugin_mode)"; then
        exit 1
    fi
    export BCN_PLUGIN_SOURCE="$resolved_bcn_mode"
    export BCN_PLUGIN_VERSION
    export SINGLEBOX_COMMAND="${command:-}"

    case "$command" in
        install-tools)
            toolchain_setup
            ;;
        setup)
            load_engine_type
            for svc in "${services[@]}"; do
                setup_service "$svc"
            done
            ;;
        start)
            load_engine_type
            if [[ "$with_bcs_coverage" -eq 1 ]]; then
                prepare_bcs_coverage_bin
            fi
            for svc in "${services[@]}"; do
                start_service "$svc"
            done
            ;;
        stop)
            for svc in "${services[@]}"; do
                stop_service "$svc"
            done
            ;;
        restart)
            for svc in "${services[@]}"; do
                restart_service "$svc"
            done
            ;;
        clean)
            for svc in "${services[@]}"; do
                clean_service "$svc"
            done
            ;;
        status)
            show_status "${services[0]}"
            ;;
        check)
            if [ "${services[0]}" != "all" ]; then
                # 按需检查: 展开组名后检查对应服务
                local svc_list=""
                for svc in "${services[@]}"; do
                    svc_list="$svc_list $(resolve_services "$svc")"
                done
                check_prereqs_for_services $svc_list
            else
                # 默认: 检查当前 all 组
                check_prereqs_for_services ${START_ORDER[*]}
            fi
            ;;
        *)
            # 默认执行当前 all 组 setup/start 流程
            setup_all_and_start
            ;;
    esac
}

# 执行主函数
main "$@"
