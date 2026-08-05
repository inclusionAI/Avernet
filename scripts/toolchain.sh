#!/usr/bin/env bash
# scripts/toolchain.sh — Development toolchain management
# Handles installation and verification of dev tools:
# node, npm, uv, protoc, openclaw, rust/cargo
#
# toolchain_setup()  → Check and install all tools (idempotent, skip-if-present)
# toolchain_check()  → Check only, return 1 if missing (dry-run, no install)
# toolchain_help()   → Print toolchain info
[[ -n "${_TOOLCHAIN_SH_LOADED:-}" ]] && return 0
_TOOLCHAIN_SH_LOADED=1

# ============ Node.js 相关 ============

# Node.js 版本要求
REQUIRED_NODE_MAJOR="22"
REQUIRED_RUST_TOOLCHAIN="${REQUIRED_RUST_TOOLCHAIN:-1.91.0}"

confirm_tool_install() {
    local prompt="$1"
    local response
    echo -e "${YELLOW}${prompt} [y/N]${NC}"
    read -r response || response=""
    [[ "$response" =~ ^[Yy]$ ]]
}

# ============ System build prerequisites ============

system_package_manager() {
    case "$(uname -s)" in
        Darwin)
            command -v brew >/dev/null 2>&1 && echo "brew" && return 0
            log_error "Homebrew is required to install missing system dependencies."
            log_error "Install it from https://brew.sh/ and rerun: ./scripts/singlebox.sh install-tools"
            return 1
            ;;
        Linux)
            local manager
            for manager in apt-get dnf yum pacman; do
                if command -v "$manager" >/dev/null 2>&1; then
                    echo "$manager"
                    return 0
                fi
            done
            log_error "No supported Linux package manager found (apt-get, dnf, yum, or pacman)."
            return 1
            ;;
        *)
            log_error "Unsupported operating system: $(uname -s)"
            return 1
            ;;
    esac
}

system_install_hint() {
    local manager="$1"
    shift
    case "$manager" in
        brew) printf 'brew install';;
        apt-get) printf 'sudo apt-get update && sudo apt-get install -y';;
        dnf) printf 'sudo dnf install -y';;
        yum) printf 'sudo yum install -y';;
        pacman) printf 'sudo pacman -S --needed';;
    esac
    printf ' %s' "$@"
    printf '\n'
}

run_system_package_install() {
    local manager="$1"
    shift
    local hint
    hint="$(system_install_hint "$manager" "$@")"

    log_info "Running: ${hint}"
    case "$manager" in
        brew)
            if brew install "$@"; then return 0; fi
            ;;
        apt-get|dnf|yum|pacman)
            if [ "$(id -u)" -eq 0 ]; then
                case "$manager" in
                    apt-get) apt-get update && apt-get install -y "$@" && return 0 ;;
                    dnf) dnf install -y "$@" && return 0 ;;
                    yum) yum install -y "$@" && return 0 ;;
                    pacman) pacman -S --needed "$@" && return 0 ;;
                esac
            elif ! command -v sudo >/dev/null 2>&1; then
                log_error "sudo is not available, so system packages cannot be installed automatically."
            else
                case "$manager" in
                    apt-get) sudo apt-get update && sudo apt-get install -y "$@" && return 0 ;;
                    dnf) sudo dnf install -y "$@" && return 0 ;;
                    yum) sudo yum install -y "$@" && return 0 ;;
                    pacman) sudo pacman -S --needed "$@" && return 0 ;;
                esac
            fi
            ;;
    esac

    log_error "System package installation was rejected or failed. Run it manually, then rerun install-tools:"
    log_error "  ${hint}"
    return 1
}

check_basic_build_environment() {
    local missing=""
    local tool
    for tool in cc c++ make perl; do
        command -v "$tool" >/dev/null 2>&1 || missing="${missing} ${tool}"
    done
    [ -z "$missing" ] && return 0

    log_error "Basic build environment is incomplete; missing:${missing}"
    case "$(uname -s)" in
        Darwin)
            log_error "Install the Xcode Command Line Tools, then rerun install-tools:"
            log_error "  xcode-select --install"
            ;;
        Linux)
            if command -v apt-get >/dev/null 2>&1; then
                log_error "Install it manually: sudo apt-get install -y build-essential"
            elif command -v dnf >/dev/null 2>&1; then
                log_error "Install it manually: sudo dnf group install -y 'Development Tools'"
            elif command -v yum >/dev/null 2>&1; then
                log_error "Install it manually: sudo yum groupinstall -y 'Development Tools'"
            elif command -v pacman >/dev/null 2>&1; then
                log_error "Install it manually: sudo pacman -S --needed base-devel"
            fi
            ;;
    esac
    return 1
}

system_command_package() {
    local manager="$1"
    local command_name="$2"
    case "${manager}:${command_name}" in
        brew:*) echo "$command_name" ;;
        apt-get:pkg-config) echo "pkg-config" ;;
        apt-get:*) echo "$command_name" ;;
        dnf:pkg-config|yum:pkg-config) echo "pkgconf-pkg-config" ;;
        pacman:pkg-config) echo "pkgconf" ;;
        dnf:*|yum:*|pacman:*) echo "$command_name" ;;
    esac
}

system_library_package() {
    local manager="$1"
    local library="$2"
    case "${manager}:${library}" in
        brew:openssl) echo "openssl@3" ;;
        brew:sqlite3) echo "sqlite" ;;
        apt-get:openssl) echo "libssl-dev" ;;
        apt-get:sqlite3) echo "libsqlite3-dev" ;;
        dnf:openssl|yum:openssl) echo "openssl-devel" ;;
        dnf:sqlite3|yum:sqlite3) echo "sqlite-devel" ;;
        pacman:openssl) echo "openssl" ;;
        pacman:sqlite3) echo "sqlite" ;;
    esac
}

refresh_homebrew_pkg_config_path() {
    [ "$(uname -s)" = "Darwin" ] || return 0
    command -v brew >/dev/null 2>&1 || return 0

    local package
    local prefix
    for package in openssl@3 sqlite; do
        prefix="$(brew --prefix "$package" 2>/dev/null || true)"
        if [ -d "${prefix}/lib/pkgconfig" ]; then
            case ":${PKG_CONFIG_PATH:-}:" in
                *":${prefix}/lib/pkgconfig:"*) ;;
                *) PKG_CONFIG_PATH="${prefix}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}" ;;
            esac
        fi
    done
    export PKG_CONFIG_PATH
}

setup_system_dependencies() {
    log_info "Checking system build environment..."
    check_basic_build_environment || return 1

    local manager=""
    local missing_commands=()
    local command_name
    for command_name in curl jq lsof pkg-config; do
        command -v "$command_name" >/dev/null 2>&1 || missing_commands+=("$command_name")
    done

    if [ "${#missing_commands[@]}" -gt 0 ]; then
        manager="$(system_package_manager)" || return 1
        local command_packages=()
        for command_name in "${missing_commands[@]}"; do
            command_packages+=("$(system_command_package "$manager" "$command_name")")
        done
        log_warn "Missing required commands:${missing_commands[*]/#/ }"
        if ! confirm_tool_install "Install missing system commands now?"; then
            log_error "Install them manually: $(system_install_hint "$manager" "${command_packages[@]}")"
            return 1
        fi
        run_system_package_install "$manager" "${command_packages[@]}" || return 1
    fi

    # Library detection deliberately happens after pkg-config installation.
    if ! command -v pkg-config >/dev/null 2>&1; then
        log_error "pkg-config is still unavailable after system dependency setup."
        return 1
    fi

    refresh_homebrew_pkg_config_path
    local missing_libraries=()
    pkg-config --exists openssl >/dev/null 2>&1 || missing_libraries+=("openssl")
    pkg-config --exists sqlite3 >/dev/null 2>&1 || missing_libraries+=("sqlite3")
    if [ "${#missing_libraries[@]}" -gt 0 ]; then
        [ -n "$manager" ] || manager="$(system_package_manager)" || return 1
        local library_packages=()
        local library
        for library in "${missing_libraries[@]}"; do
            library_packages+=("$(system_library_package "$manager" "$library")")
        done
        log_warn "Missing development libraries:${missing_libraries[*]/#/ }"
        if ! confirm_tool_install "Install missing development libraries now?"; then
            log_error "Install them manually: $(system_install_hint "$manager" "${library_packages[@]}")"
            return 1
        fi
        run_system_package_install "$manager" "${library_packages[@]}" || return 1
        refresh_homebrew_pkg_config_path
        for library in "${missing_libraries[@]}"; do
            if ! pkg-config --exists "$library" >/dev/null 2>&1; then
                log_error "${library} is still not visible to pkg-config after installation."
                log_error "Fix PKG_CONFIG_PATH or install the development package, then rerun install-tools."
                return 1
            fi
        done
    fi

    log_info "System dependencies are ready."
}

# 检测 shell profile 文件
detect_shell_profile() {
    if [ -n "$ZSH_VERSION" ]; then
        echo "$HOME/.zshrc"
    elif [ -n "$BASH_VERSION" ]; then
        if [ -f "$HOME/.bashrc" ]; then
            echo "$HOME/.bashrc"
        else
            echo "$HOME/.bash_profile"
        fi
    else
        echo "$HOME/.profile"
    fi
}

# 将 nvm 配置追加到 shell profile
_append_nvm_to_profile() {
    local profile="$1"
    local nvm_snippet='
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
'

    if [ -f "$profile" ] && ! grep -q 'NVM_DIR' "$profile"; then
        log_info "Adding nvm to $profile..."
        echo "$nvm_snippet" >> "$profile"
    fi
}

# 确保 nvm 已加载
_ensure_nvm_loaded() {
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"

    if [ -s "$NVM_DIR/nvm.sh" ]; then
        source "$NVM_DIR/nvm.sh"
        return 0
    fi
    return 1
}

# 根据 node 路径获取对应的 npm 路径
_npm_for_node() {
    local node_path="$1"
    echo "$(dirname "$node_path")/npm"
}

# 检查当前 Node.js 版本是否满足要求 (静默检查，不打印日志)
check_node_version() {
    local node_path="$1"
    if [ -x "$node_path" ]; then
        local version=$("$node_path" -v 2>/dev/null)
        local major_version=$(echo "$version" | sed 's/^v//' | cut -d'.' -f1)
        if [ "$major_version" -ge "$REQUIRED_NODE_MAJOR" ]; then
            return 0
        else
            return 1
        fi
    fi
    return 1
}

# 通过 nvm 安装 Node.js
install_node_via_nvm() {
    log_info "Installing Node.js v${REQUIRED_NODE_MAJOR} via nvm (without switching current version)..."

    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"

    # 安装 nvm（如果不存在）
    if [ ! -s "$NVM_DIR/nvm.sh" ]; then
        log_info "Installing nvm..."
        local nvm_installer_url="https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh"
        if [ "${USE_CN_MIRROR:-}" = "1" ]; then
            nvm_installer_url="https://gitee.com/mirrors/nvm/raw/v0.40.1/install.sh"
        fi
        if ! curl -fsSL "$nvm_installer_url" | bash; then
            log_error "nvm installation failed (url=$nvm_installer_url)"
            return 1
        fi

        if [ ! -s "$NVM_DIR/nvm.sh" ]; then
            log_error "nvm installation failed (nvm.sh not found at $NVM_DIR/nvm.sh)"
            return 1
        fi
    else
        log_info "nvm already installed at $NVM_DIR"
    fi

    # 写入 shell profile
    local shell_profile
    shell_profile="$(detect_shell_profile)"
    _append_nvm_to_profile "$shell_profile"

    # 在当前 session 加载 nvm（仅用于 nvm install 命令）
    if ! _ensure_nvm_loaded; then
        log_error "nvm command not available after sourcing nvm.sh"
        return 1
    fi

    log_info "nvm $(nvm --version) loaded"

    # 安装 Node.js，但不 nvm use，不改 default alias
    if ! nvm install "$REQUIRED_NODE_MAJOR"; then
        log_error "nvm install failed"
        return 1
    fi

    # 通过绝对路径找到刚安装的 node，而不是 nvm use
    local installed_node="$NVM_DIR/versions/node/$(nvm version "$REQUIRED_NODE_MAJOR")/bin/node"
    if [ ! -x "$installed_node" ]; then
        log_error "Node.js installation via nvm succeeded but binary not found at $installed_node"
        return 1
    fi

    log_info "Node.js $("$installed_node" -v) installed via nvm at: $installed_node"
    log_info "Your current node version and nvm default alias were NOT changed"
    log_info "To make v${REQUIRED_NODE_MAJOR} your default: nvm alias default ${REQUIRED_NODE_MAJOR}"
    return 0
}

# 设置 Node.js 环境
setup_node() {
    log_info "Checking Node.js installation..."

    # 检查系统 node
    local system_node=$(command -v node 2>/dev/null)
    if [ -n "$system_node" ] && check_node_version "$system_node"; then
        log_info "Using system Node.js: $system_node"
        return 0
    fi

    # 检查 nvm 安装的 node
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    if [ -s "$NVM_DIR/nvm.sh" ]; then
        source "$NVM_DIR/nvm.sh"
        local nvm_node="$NVM_DIR/versions/node/v${REQUIRED_NODE_MAJOR}"
        # 查找匹配版本的 node
        for node_dir in "$NVM_DIR/versions/node/v${REQUIRED_NODE_MAJOR}"*; do
            if [ -x "$node_dir/bin/node" ] && check_node_version "$node_dir/bin/node"; then
                log_info "Using nvm Node.js: $node_dir/bin/node"
                export PATH="$node_dir/bin:$PATH"
                return 0
            fi
        done
    fi

    # 需要安装 Node.js
    log_info "Node.js not found or version too old, installing..."
    if ! install_node_via_nvm; then
        log_error "Failed to install Node.js"
        return 1
    fi

    # 加载新安装的 node
    if _ensure_nvm_loaded; then
        local new_node="$NVM_DIR/versions/node/$(nvm version "$REQUIRED_NODE_MAJOR")/bin"
        export PATH="$new_node:$PATH"
        log_info "Node.js added to PATH: $new_node"
    fi
}

# 检查 Node.js 是否满足要求 (只检不装)
check_node_available() {
    local system_node=$(command -v node 2>/dev/null)
    if [ -n "$system_node" ] && check_node_version "$system_node"; then
        return 0
    fi

    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    if [ -s "$NVM_DIR/nvm.sh" ]; then
        source "$NVM_DIR/nvm.sh" 2>/dev/null
        for node_dir in "$NVM_DIR/versions/node/v${REQUIRED_NODE_MAJOR}"*; do
            if [ -x "$node_dir/bin/node" ] && check_node_version "$node_dir/bin/node"; then
                return 0
            fi
        done
    fi

    return 1
}

# ============ npm 相关 ============

# 检查 npm 是否存在 (静默检查，不打印日志)
check_npm() {
    command -v npm &> /dev/null
}

ensure_npm_available() {
    if check_npm; then
        log_info "npm found: $(npm --version 2>&1)"
        return 0
    fi

    log_warn "npm not found. npm is normally installed with Node.js."
    log_info "This installer can install Node.js v${REQUIRED_NODE_MAJOR} via nvm into ${NVM_DIR:-$HOME/.nvm}."
    log_info "It will not change your nvm default alias."
    if confirm_tool_install "Install Node.js v${REQUIRED_NODE_MAJOR} with npm now?"; then
        install_node_via_nvm || return 1
        if _ensure_nvm_loaded; then
            local new_node="$NVM_DIR/versions/node/$(nvm version "$REQUIRED_NODE_MAJOR")/bin"
            export PATH="$new_node:$PATH"
        fi
    else
        log_error "npm is required. Install Node.js ${REQUIRED_NODE_MAJOR}+ with npm, then rerun install-tools."
        return 1
    fi

    if check_npm; then
        log_info "npm found: $(npm --version 2>&1)"
        return 0
    fi

    log_error "npm is still not available after Node.js setup."
    return 1
}

# ============ OpenClaw 安装 ============

# 安装 openclaw
install_openclaw() {
    log_info "Installing openclaw ${OPENCLAW_VERSION}..."

    # Use the public npm client only. Do not require private package managers.
    if command -v npm &> /dev/null; then
        log_info "npm global prefix: $(npm prefix -g 2>&1)"
        npm i "openclaw@${OPENCLAW_VERSION}" -g --registry="${NPM_REGISTRY_URL}" --strict-ssl=false
    else
        log_error "npm not found. Please install Node.js with npm first."
        return 1
    fi

    # 验证安装是否成功
    if check_openclaw_installed; then
        local installed_version=$(get_openclaw_version)
        log_info "openclaw ${installed_version} installed successfully"

        # Keep the user's existing OpenClaw model config unchanged by default.
        copy_openclaw_config

        return 0
    else
        log_error "Failed to install openclaw"
        return 1
    fi
}

# 检查并安装 openclaw
setup_openclaw() {
    log_info "Checking openclaw installation..."

    local current_version=$(get_openclaw_version)
    local required_min_version=$(extract_version_from_text "${OPENCLAW_VERSION}")

    if [ -n "$current_version" ]; then
        if [ -n "$required_min_version" ] && version_at_least "$current_version" "$required_min_version"; then
            log_info "openclaw ${current_version} satisfies required >= ${required_min_version}"
            # Report the config source without overwriting local model settings.
            copy_openclaw_config
            return 0
        else
            log_warn "openclaw version below minimum: installed ${current_version}, required >= ${required_min_version:-$OPENCLAW_VERSION}"
            if confirm_tool_install "是否安装 openclaw ${OPENCLAW_VERSION}?"; then
                install_openclaw
                return $?
            else
                log_warn "Skipping openclaw installation, using existing version ${current_version}"
                # Report the config source without overwriting local model settings.
                copy_openclaw_config
                return 0
            fi
        fi
    else
        log_info "openclaw not found"
        if confirm_tool_install "是否安装 openclaw ${OPENCLAW_VERSION}?"; then
            install_openclaw
            return $?
        else
            log_error "openclaw is required for the gateway service"
            return 1
        fi
    fi
}

# ============ toolchain 主接口 ============

# 检查所有工具 (只检不装)
# 委托给 per-service prereqs 系统，检查全部服务的依赖
toolchain_check() {
    check_prereqs_for_services ${START_ORDER[*]}
}

rustup_target_triple() {
    local os
    local arch
    os="$(uname -s)"
    arch="$(uname -m)"

    case "${os}:${arch}" in
        Darwin:x86_64)
            echo "x86_64-apple-darwin"
            ;;
        Darwin:arm64|Darwin:aarch64)
            echo "aarch64-apple-darwin"
            ;;
        Linux:x86_64|Linux:amd64)
            echo "x86_64-unknown-linux-gnu"
            ;;
        Linux:aarch64|Linux:arm64)
            echo "aarch64-unknown-linux-gnu"
            ;;
        *)
            log_error "Unsupported Rust installer target: ${os}/${arch}"
            return 1
            ;;
    esac
}

load_existing_rust_from_home() {
    local cargo_home="${CARGO_HOME:-$HOME/.cargo}"
    if [ -x "${cargo_home}/bin/cargo" ] && [ -x "${cargo_home}/bin/rustc" ]; then
        log_warn "Rust/Cargo found under ${cargo_home}/bin but not in current PATH."
        export PATH="${cargo_home}/bin:${PATH}"
        if check_rust_installed; then
            log_info "Loaded Rust/Cargo from ${cargo_home}/bin for this shell."
            return 0
        fi
    fi
    return 1
}

install_rust_via_rustup() {
    local cargo_home="${CARGO_HOME:-$HOME/.cargo}"
    local rustup_home="${RUSTUP_HOME:-$HOME/.rustup}"

    log_info "Installing Rust/Cargo ${REQUIRED_RUST_TOOLCHAIN} via rustup..."
    log_info "CARGO_HOME: ${cargo_home}"
    log_info "RUSTUP_HOME: ${rustup_home}"

    if [ "${USE_CN_MIRROR:-}" = "1" ]; then
        local target
        target="$(rustup_target_triple)" || return 1
        local rustup_update_root="${RUSTUP_UPDATE_ROOT:-https://mirrors.ustc.edu.cn/rust-static/rustup}"
        local work
        local rustup_init
        work="$(mktemp -d -t rustup-init.XXXXXX)"
        rustup_init="${work}/rustup-init"

        if ! curl --proto '=https' --tlsv1.2 -sSfL "${rustup_update_root}/dist/${target}/rustup-init" -o "${rustup_init}"; then
            rm -rf "${work}"
            log_error "Failed to download rustup-init from ${rustup_update_root}"
            return 1
        fi
        chmod +x "${rustup_init}"
        if ! "${rustup_init}" -y --profile minimal --default-toolchain "${REQUIRED_RUST_TOOLCHAIN}"; then
            rm -rf "${work}"
            log_error "rustup installation failed"
            return 1
        fi
        rm -rf "${work}"
    else
        local work
        local rustup_script
        work="$(mktemp -d -t rustup-script.XXXXXX)"
        rustup_script="${work}/rustup.sh"

        if ! curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o "${rustup_script}"; then
            rm -rf "${work}"
            log_error "Failed to download rustup installer from https://sh.rustup.rs"
            return 1
        fi
        if ! sh "${rustup_script}" -y --profile minimal --default-toolchain "${REQUIRED_RUST_TOOLCHAIN}"; then
            rm -rf "${work}"
            log_error "rustup installation failed"
            return 1
        fi
        rm -rf "${work}"
    fi

    if [ -f "${cargo_home}/env" ]; then
        # shellcheck source=/dev/null
        . "${cargo_home}/env"
    fi
    export PATH="${cargo_home}/bin:${PATH}"

    if check_rust_installed; then
        log_info "Rust/Cargo installed: $(rustc --version 2>&1 | head -1)"
        return 0
    fi

    log_error "Rust/Cargo is still not available after rustup installation."
    return 1
}

setup_rust() {
    log_info "Checking Rust/Cargo..."

    if check_rust_installed; then
        log_info "Rust/Cargo found: $(rustc --version 2>&1 | head -1)"
        return 0
    fi

    if load_existing_rust_from_home; then
        log_info "Rust/Cargo found: $(rustc --version 2>&1 | head -1)"
        return 0
    fi

    log_warn "Rust/Cargo not found in current PATH. It is required for building BCS."
    log_info "This installer can install Rust via rustup into ${CARGO_HOME:-$HOME/.cargo} and ${RUSTUP_HOME:-$HOME/.rustup}."
    log_info "rustup may update your shell profile so cargo is available in new terminals."
    if confirm_tool_install "Install Rust/Cargo ${REQUIRED_RUST_TOOLCHAIN} now?"; then
        install_rust_via_rustup
        return $?
    fi

    log_error "Rust/Cargo is required for BCS. Install Rust ${REQUIRED_RUST_TOOLCHAIN}+ and rerun install-tools."
    return 1
}

setup_protobuf_interactive() {
    log_info "Checking protobuf..."

    if check_protobuf_installed; then
        log_info "protobuf found: $(get_protobuf_version)"
        return 0
    fi

    log_warn "protobuf/protoc not found. It is required for building BCS."
    case "$(uname -s)" in
        Darwin)
            if command -v brew &> /dev/null; then
                log_info "This installer can run: brew install protobuf"
                log_info "Homebrew may update or install packages outside this repository."
            else
                log_error "Homebrew not found. Install protobuf manually, then rerun install-tools."
                return 1
            fi
            ;;
        Linux)
            log_info "This installer can use your system package manager to install protobuf-compiler."
            log_info "It may require sudo and may modify system packages."
            ;;
        *)
            log_error "Unsupported operating system: $(uname -s). Install protobuf manually, then rerun install-tools."
            return 1
            ;;
    esac

    if confirm_tool_install "Install protobuf/protoc now?"; then
        install_protobuf || return 1
    else
        log_error "protobuf/protoc is required for BCS. Install it manually and rerun install-tools."
        return 1
    fi

    if check_protobuf_installed; then
        log_info "protobuf installed successfully: $(get_protobuf_version)"
        return 0
    fi

    log_error "protobuf/protoc is still not available after installation."
    return 1
}

# 幂等写入 ~/.cargo/config.toml（仅 USE_CN_MIRROR=1 且文件不存在时）
_apply_cargo_mirror_config() {
    [ "${USE_CN_MIRROR:-}" = "1" ] || return 0
    local cargo_config="${CARGO_HOME:-$HOME/.cargo}/config.toml"
    if [ -f "$cargo_config" ]; then
        log_info "cargo config already exists at $cargo_config; not overwriting"
        return 0
    fi
    mkdir -p "$(dirname "$cargo_config")"
    cat > "$cargo_config" <<'EOF'
[source.crates-io]
replace-with = "mirror"

[source.mirror]
registry = "sparse+https://mirrors.aliyun.com/crates.io-index/"
EOF
    log_info "Wrote cargo mirror config to $cargo_config (sparse+aliyun)"
}

# 安装/升级所有工具 (幂等)
toolchain_setup() {
    echo ""
    log_info "Setting up toolchain..."
    echo ""

    _apply_cargo_mirror_config

    # Step 1: System dependencies
    log_info "[1/7] Checking system dependencies..."
    setup_system_dependencies || return 1
    echo ""

    # Step 2: Node.js
    log_info "[2/7] Setting up Node.js..."
    setup_node || return 1
    echo ""

    # Step 3: npm
    log_info "[3/7] Checking npm..."
    ensure_npm_available || return 1
    echo ""

    # Step 4: uv
    log_info "[4/7] Setting up uv..."
    if ! check_uv_installed; then
        auto_install_uv || return 1
    else
        log_info "uv already installed"
    fi
    echo ""

    # Step 5: openclaw
    log_info "[5/7] Setting up openclaw..."
    setup_openclaw || return 1
    echo ""

    # Step 6: Rust/Cargo
    log_info "[6/7] Setting up Rust/Cargo..."
    setup_rust || return 1
    echo ""

    # Step 7: Protobuf
    log_info "[7/7] Setting up protobuf..."
    setup_protobuf_interactive || return 1
    echo ""

    log_info "Toolchain setup complete!"
    echo ""
}

# 工具链帮助
toolchain_help() {
    echo "toolchain - Development tools (node, npm, uv, protoc, openclaw, rust)"
}
