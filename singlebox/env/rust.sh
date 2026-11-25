#!/usr/bin/env bash
# Rust installation and selection for Singlebox builds.
REQUIRED_RUST_TOOLCHAIN="1.94.0"
export RUSTUP_TOOLCHAIN="$REQUIRED_RUST_TOOLCHAIN"

check_required_rust_toolchain() {
    check_rust_installed || return 1
    # Probe without triggering rustup's automatic installation in check paths.
    if command -v rustup >/dev/null 2>&1; then
        rustup run "$REQUIRED_RUST_TOOLCHAIN" rustc --version >/dev/null 2>&1 || return 1
    fi
    [[ "$(rustc --version 2>/dev/null)" == "rustc ${REQUIRED_RUST_TOOLCHAIN} "* ]]
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

load_rust_environment() {
    local cargo_home="${CARGO_HOME:-$HOME/.cargo}"
    local cargo_bin="${cargo_home}/bin"

    if [ -f "${cargo_home}/env" ]; then
        # shellcheck source=/dev/null
        . "${cargo_home}/env"
    fi
    if [ -d "$cargo_bin" ]; then
        case ":${PATH}:" in
            *":${cargo_bin}:"*) ;;
            *) export PATH="${cargo_bin}:${PATH}" ;;
        esac
    fi
}

load_existing_rust_from_home() {
    local cargo_home="${CARGO_HOME:-$HOME/.cargo}"
    if [ -x "${cargo_home}/bin/cargo" ] && [ -x "${cargo_home}/bin/rustc" ]; then
        log_warn "Rust/Cargo found under ${cargo_home}/bin but not in current PATH."
        load_rust_environment
        if check_required_rust_toolchain; then
            log_info "Loaded Rust/Cargo from ${cargo_home}/bin for this shell."
            _toolchain_require_shell_reload
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

    if command -v rustup >/dev/null 2>&1; then
        rustup toolchain install "$REQUIRED_RUST_TOOLCHAIN" --profile minimal --component llvm-tools || return 1
    elif [ "${USE_CN_MIRROR:-}" = "1" ]; then
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
        if ! "${rustup_init}" -y --profile minimal --default-toolchain "${REQUIRED_RUST_TOOLCHAIN}" --component llvm-tools; then
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
        if ! sh "${rustup_script}" -y --profile minimal --default-toolchain "${REQUIRED_RUST_TOOLCHAIN}" --component llvm-tools; then
            rm -rf "${work}"
            log_error "rustup installation failed"
            return 1
        fi
        rm -rf "${work}"
    fi

    load_rust_environment

    if check_required_rust_toolchain; then
        log_info "Rust/Cargo installed: $(rustc --version 2>&1 | head -1)"
        _toolchain_require_shell_reload
        return 0
    fi

    log_error "Rust/Cargo ${REQUIRED_RUST_TOOLCHAIN} is not active after installation. Check PATH for a non-rustup compiler."
    return 1
}

setup_rust() {
    log_info "Checking Rust/Cargo..."

    if check_required_rust_toolchain; then
        log_info "Rust/Cargo found: $(rustc --version 2>&1 | head -1)"
        return 0
    fi

    if load_existing_rust_from_home; then
        log_info "Rust/Cargo found: $(rustc --version 2>&1 | head -1)"
        return 0
    fi

    log_warn "Rust/Cargo ${REQUIRED_RUST_TOOLCHAIN} is required for building BCS and is not available in the current environment."
    log_info "This installer can install Rust via rustup into ${CARGO_HOME:-$HOME/.cargo} and ${RUSTUP_HOME:-$HOME/.rustup}."
    log_info "rustup may update your shell profile so cargo is available in new terminals."
    if confirm_tool_install "Install Rust/Cargo ${REQUIRED_RUST_TOOLCHAIN} now?"; then
        install_rust_via_rustup
        return $?
    fi

    log_error "Rust/Cargo is required for BCS. Install Rust ${REQUIRED_RUST_TOOLCHAIN} and rerun install-tools."
    return 1
}
