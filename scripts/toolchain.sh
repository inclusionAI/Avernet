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

    # Step 1: Node.js
    log_info "[1/6] Setting up Node.js..."
    setup_node
    echo ""

    # Step 2: npm
    log_info "[2/6] Checking npm..."
    ensure_npm_available || return 1
    echo ""

    # Step 3: uv
    log_info "[3/6] Setting up uv..."
    if ! check_uv_installed; then
        auto_install_uv
    else
        log_info "uv already installed"
    fi
    echo ""

    # Step 4: openclaw
    log_info "[4/6] Setting up openclaw..."
    setup_openclaw
    echo ""

    # Step 5: Rust/Cargo
    log_info "[5/6] Setting up Rust/Cargo..."
    setup_rust || return 1
    echo ""

    # Step 6: Protobuf
    log_info "[6/6] Setting up protobuf..."
    setup_protobuf_interactive || return 1
    echo ""

    log_info "Toolchain setup complete!"
    echo ""
}

# 工具链帮助
toolchain_help() {
    echo "toolchain - Development tools (node, npm, uv, protoc, openclaw, rust)"
}
