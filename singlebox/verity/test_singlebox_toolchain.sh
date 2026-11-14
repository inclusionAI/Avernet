#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_eq() {
  local expected="$1"
  local actual="$2"
  local label="$3"
  [ "$expected" = "$actual" ] || fail "${label}: expected '${expected}', got '${actual}'"
}

assert_contains() {
  local expected="$1"
  local actual="$2"
  local label="$3"
  case "$actual" in
    *"$expected"*) ;;
    *) fail "${label}: expected output to contain '${expected}'" ;;
  esac
}

YELLOW=""
NC=""
log_info() { :; }
log_warn() { :; }
log_error() { :; }

# Functions referenced by toolchain.sh but supplied by utils.sh in production.
check_rust_installed() { return 1; }
check_protobuf_installed() { return 1; }

REQUIRED_RUST_TOOLCHAIN="stable"
RUSTUP_TOOLCHAIN="stable"
# shellcheck source=/dev/null
source "${ROOT}/singlebox/env/toolchain.sh"

test_default_rust_toolchain() {
  assert_eq "1.94.0" "$REQUIRED_RUST_TOOLCHAIN" "pinned Rust toolchain"
  assert_eq "1.94.0" "$RUSTUP_TOOLCHAIN" "selected Rust toolchain"
  bash -c '[ "$RUSTUP_TOOLCHAIN" = "1.94.0" ]' || fail "Rust selection should reach child builds"
}

test_existing_required_rust_skips_install() (
  check_rust_installed() { return 0; }
  rustup() {
    assert_eq "run 1.94.0 rustc --version" "$*" "installed toolchain probe"
  }
  rustc() { printf 'rustc 1.94.0 (4a4ef493e 2026-03-02)\n'; }
  confirm_tool_install() { fail "matching Rust must not request installation"; }

  setup_rust || fail "matching Rust should be accepted"
)

test_existing_rustup_installs_missing_required_rust() (
  local installed=0 install_count=0
  check_rust_installed() { return 0; }
  load_existing_rust_from_home() { return 1; }
  load_rust_environment() { :; }
  _toolchain_require_shell_reload() { :; }
  confirm_tool_install() { return 0; }
  curl() { fail "existing rustup must not download another installer"; }
  rustup() {
    case "$*" in
      "run 1.94.0 rustc --version") [ "$installed" = 1 ] ;;
      "toolchain install 1.94.0 --profile minimal --component llvm-tools")
        installed=1
        install_count=$((install_count + 1))
        ;;
      *) fail "unexpected rustup command: $*" ;;
    esac
  }
  rustc() {
    if [ "$installed" = 1 ]; then
      printf 'rustc 1.94.0 (4a4ef493e 2026-03-02)\n'
    else
      printf 'rustc 1.99.1 (other compiler)\n'
    fi
  }

  setup_rust || fail "existing rustup should install the required release"
  assert_eq 1 "$install_count" "required release installed once"
  setup_rust || fail "repeated setup should accept the installed release"
  assert_eq 1 "$install_count" "repeated setup should not reinstall"
)

test_fresh_rust_install_uses_required_release() (
  local temp_dir installed=0
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' EXIT
  export PATH="/usr/bin:/bin" TEST_RUST_INSTALL_LOG="$temp_dir/install-args"
  USE_CN_MIRROR=""
  check_rust_installed() { [ "$installed" = 1 ]; }
  load_existing_rust_from_home() { return 1; }
  load_rust_environment() { installed=1; }
  _toolchain_require_shell_reload() { :; }
  confirm_tool_install() { return 0; }
  rustc() { printf 'rustc 1.94.0 (4a4ef493e 2026-03-02)\n'; }
  curl() {
    local output_path=""
    while [ "$#" -gt 0 ]; do
      if [ "$1" = "-o" ]; then
        output_path="$2"
        break
      fi
      shift
    done
    [ -n "$output_path" ] || fail "Rust installer download must specify a destination"
    cat > "$output_path" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" > "$TEST_RUST_INSTALL_LOG"
EOF
  }

  setup_rust || fail "fresh Rust installation should succeed"
  assert_eq "-y --profile minimal --default-toolchain 1.94.0 --component llvm-tools" \
    "$(<"$TEST_RUST_INSTALL_LOG")" "fresh installer toolchain and components"
)

test_required_rust_install_failure_is_reported() (
  check_rust_installed() { return 0; }
  load_existing_rust_from_home() { return 1; }
  confirm_tool_install() { return 0; }
  rustup() { return 1; }
  rustc() { printf 'rustc 1.99.1 (other compiler)\n'; }

  if setup_rust; then
    fail "failed toolchain installation must fail setup"
  fi
)

test_wrong_rust_cannot_be_accepted_after_declining_install() (
  check_rust_installed() { return 0; }
  load_existing_rust_from_home() { return 1; }
  confirm_tool_install() { return 1; }
  rustup() { return 1; }
  rustc() { printf 'rustc 1.99.1 (other compiler)\n'; }
  install_rust_via_rustup() { fail "declined installation must not run"; }

  if setup_rust; then
    fail "a different Rust release must not satisfy the pinned toolchain"
  fi
)

test_bcs_check_only_validates_required_rust_without_installing() (
  local temp_dir tool output
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' EXIT
  mkdir -p "$temp_dir/bin" "$temp_dir/cargo"
  printf '%s\n' 'replace-with = "aliyun"' > "$temp_dir/cargo/config.toml"
  for tool in cargo cargo-nextest cargo-llvm-cov protoc; do
    printf '%s\n' '#!/bin/sh' 'exit 0' > "$temp_dir/bin/$tool"
  done
  cat > "$temp_dir/bin/rustc" <<'EOF'
#!/bin/sh
if [ -f "$CARGO_HOME/installed-1.94.0" ]; then
  printf 'rustc 1.94.0 (test compiler)\n'
else
  printf 'rustc %s (test compiler)\n' "$TEST_RUST_RELEASE"
fi
EOF
  cat > "$temp_dir/bin/rustup" <<'EOF'
#!/bin/sh
[ "$RUSTUP_TOOLCHAIN" = 1.94.0 ] || exit 90
case "$*" in
  'run 1.94.0 rustc --version') exit 0 ;;
  'component list --toolchain 1.94.0 --installed') printf 'llvm-tools-test-target\n' ;;
  'toolchain install 1.94.0 --profile minimal')
    [ "${TEST_RUST_ALLOW_INSTALL:-}" = 1 ] || exit 92
    touch "$CARGO_HOME/installed-1.94.0"
    ;;
  *) printf 'unexpected mutation: %s\n' "$*" >&2; exit 91 ;;
esac
EOF
  chmod +x "$temp_dir/bin/"*
  export CARGO_HOME="$temp_dir/cargo" PATH="$temp_dir/bin:/usr/bin:/bin"
  export TEST_RUST_RELEASE=1.94.0 RUSTUP_TOOLCHAIN=stable
  bash "$ROOT/apps/bcs/scripts/setup_dev_env.sh" --check >/dev/null \
    || fail "check-only should accept Rust 1.94.0"
  export TEST_RUST_RELEASE=1.99.1
  if output="$(bash "$ROOT/apps/bcs/scripts/setup_dev_env.sh" --check 2>&1)"; then
    fail "check-only must reject a different Rust compiler"
  fi
  assert_contains "Rust/Cargo 1.94.0 is not active" "$output" "wrong compiler diagnostic"
  [ ! -f "$CARGO_HOME/installed-1.94.0" ] || fail "check-only must not install Rust"
  export TEST_RUST_ALLOW_INSTALL=1
  bash "$ROOT/apps/bcs/scripts/setup_dev_env.sh" >/dev/null \
    || fail "existing rustup without a Cargo env file should install the pinned release"
  [ -f "$CARGO_HOME/installed-1.94.0" ] || fail "setup must install the pinned release"
)

test_singlebox_prereqs_do_not_install_missing_rust() (
  local temp_dir output
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' EXIT
  LOG_DIR="$temp_dir" DEP_DIR="$temp_dir" BCS_DIR="$temp_dir" CYAN=""
  # shellcheck source=/dev/null
  source "${ROOT}/singlebox/modules/bcs.sh"
  check_rust_installed() { return 0; }
  rustup() {
    if [ "$*" = "run 1.94.0 rustc --version" ]; then
      touch "$temp_dir/probed"
      return 1
    fi
    touch "$temp_dir/mutation"
    return 1
  }
  rustc() {
    touch "$temp_dir/rustc-invoked"
    printf 'rustc 1.94.0 (test compiler)\n'
  }
  check_protobuf_installed() { return 0; }
  get_protobuf_version() { printf 'test protoc\n'; }
  check_port_available() { return 0; }
  prereq_ok() { :; }
  prereq_error() { printf '%s\n' "$*"; }

  if output="$(bcs_prereqs 2>&1)"; then
    fail "Singlebox prerequisites must reject a missing Rust 1.94.0 toolchain"
  fi
  assert_contains "Rust/Cargo 1.94.0 is not available" "$output" "missing toolchain diagnostic"
  [ -f "$temp_dir/probed" ] || fail "prerequisites must probe the required toolchain"
  [ ! -f "$temp_dir/rustc-invoked" ] || fail "missing toolchain must not invoke rustc and trigger automatic installation"
  [ ! -f "$temp_dir/mutation" ] || fail "prerequisites must not install the missing toolchain"
)

test_command_package_mapping() {
  assert_eq "pkgconf-pkg-config" "$(system_command_package dnf pkg-config)" "Fedora pkg-config package"
  assert_eq "pkgconf" "$(system_command_package pacman pkg-config)" "Arch pkg-config package"
  assert_eq "jq" "$(system_command_package apt-get jq)" "Debian jq package"
  assert_eq "lsof" "$(system_command_package brew lsof)" "Homebrew lsof package"
}

test_library_package_mapping() {
  assert_eq "openssl@3" "$(system_library_package brew openssl)" "Homebrew OpenSSL package"
  assert_eq "sqlite" "$(system_library_package brew sqlite3)" "Homebrew SQLite package"
  assert_eq "libssl-dev" "$(system_library_package apt-get openssl)" "Debian OpenSSL package"
  assert_eq "libsqlite3-dev" "$(system_library_package apt-get sqlite3)" "Debian SQLite package"
  assert_eq "openssl-devel" "$(system_library_package dnf openssl)" "Fedora OpenSSL package"
  assert_eq "sqlite-devel" "$(system_library_package yum sqlite3)" "RPM SQLite package"
}

test_manual_install_hints() {
  assert_eq "brew install curl jq" "$(system_install_hint brew curl jq)" "Homebrew hint"
  assert_eq "sudo apt-get update && sudo apt-get install -y curl jq" "$(system_install_hint apt-get curl jq)" "apt hint"
  assert_eq "sudo pacman -S --needed curl jq" "$(system_install_hint pacman curl jq)" "pacman hint"
}

test_basic_build_environment_on_current_host() {
  check_basic_build_environment || fail "current test host should provide cc, c++, make, and perl"
}

test_failed_install_prints_manual_command() {
  brew() { return 1; }
  log_error() { printf '%s\n' "$*"; }
  output="$(run_system_package_install brew jq 2>&1)" && fail "failed brew install should return non-zero"
  case "$output" in
    *"brew install jq"*) ;;
    *) fail "failed install did not print the manual command" ;;
  esac
  log_error() { :; }
}

test_claude_code_existing_cli_skips_install() (
    local temp_dir cli_path
    temp_dir="$(mktemp -d)"
    cli_path="${temp_dir}/claude"
    printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$cli_path"
    chmod +x "$cli_path"

    export CLAUDE_CODE_PATH="$cli_path"
    npm() { fail "npm should not run when CLAUDE_CODE_PATH is executable"; }

    setup_claude_code || fail "existing Claude Code CLI should be accepted"
    assert_eq "$cli_path" "$CLAUDE_CODE_PATH" "existing Claude Code path"
)

test_claude_code_default_response_installs_missing_cli() (
    local temp_dir bin_dir args_path
    temp_dir="$(mktemp -d)"
    bin_dir="${temp_dir}/bin"
    TEST_CLAUDE_CLI_PATH="${bin_dir}/claude"
    args_path="${temp_dir}/npm-args"
    mkdir -p "$bin_dir"

    export PATH="${bin_dir}:/usr/bin:/bin"
    export TEST_CLAUDE_CLI_PATH
    unset CLAUDE_CODE_PATH
    npm() {
        if [ "$1" = "prefix" ] && [ "$2" = "-g" ]; then
            printf '%s\n' "$temp_dir"
            return 0
        fi
        assert_eq "install -g @anthropic-ai/claude-code --registry=https://registry.npmmirror.com" "$*" "Claude Code npm install arguments"
        printf '%s\n' "$*" > "$args_path"
        printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$TEST_CLAUDE_CLI_PATH"
        chmod +x "$TEST_CLAUDE_CLI_PATH"
    }

    setup_claude_code < <(printf '\n') || fail "empty response should install missing Claude Code"
    assert_eq "install -g @anthropic-ai/claude-code --registry=https://registry.npmmirror.com" "$(<"$args_path")" "recorded Claude Code npm install arguments"
)

test_claude_code_decline_skips_install() (
    local temp_dir
    temp_dir="$(mktemp -d)"

    export PATH="/usr/bin:/bin"
    unset CLAUDE_CODE_PATH
    npm() {
        if [ "$1" = "prefix" ] && [ "$2" = "-g" ]; then
            printf '%s\n' "$temp_dir"
            return 0
        fi
        fail "npm install should not run when Claude Code installation is declined"
    }

    setup_claude_code < <(printf 'n\n') || fail "declining Claude Code should allow the toolchain to continue"
    [ -z "${CLAUDE_CODE_PATH:-}" ] || fail "Claude Code path should remain unset when installation is declined"
)

test_toolchain_setup_continues_after_claude_code_skip() (
    local temp_dir completed_steps=""
    temp_dir="$(mktemp -d)"

    record_step() {
        completed_steps+="${completed_steps:+,}$1"
    }
    _apply_cargo_mirror_config() { :; }
    setup_system_dependencies() { record_step system; }
    setup_node() { record_step node; }
    ensure_npm_available() { record_step npm; }
    ensure_uv() { record_step uv; }
    ensure_uv_managed_python() { record_step python; }
    check_python_version_file() { :; }
    setup_openclaw() { record_step openclaw; }
    setup_rust() { record_step rust; }
    setup_protobuf_interactive() { record_step protobuf; }
    export PATH="/usr/bin:/bin"
    unset CLAUDE_CODE_PATH
    npm() {
        if [ "$1" = "prefix" ] && [ "$2" = "-g" ]; then
            printf '%s\n' "$temp_dir"
            return 0
        fi
        fail "npm install should not run when Claude Code installation is declined"
    }

    toolchain_setup < <(printf 'n\n') || fail "declining Claude Code should not stop toolchain setup"
    assert_eq "system,node,npm,uv,python,openclaw,rust,protobuf" "$completed_steps" "steps after declining Claude Code"
)

test_claude_code_installs_missing_cli() (
    local temp_dir bin_dir args_path
    temp_dir="$(mktemp -d)"
    bin_dir="${temp_dir}/bin"
    TEST_CLAUDE_CLI_PATH="${bin_dir}/claude"
    args_path="${temp_dir}/npm-args"
    mkdir -p "$bin_dir"

    export PATH="${bin_dir}:/usr/bin:/bin"
    export TEST_CLAUDE_CLI_PATH
    unset CLAUDE_CODE_PATH
    confirm_tool_install() { return 0; }
    npm() {
        if [ "$1" = "prefix" ] && [ "$2" = "-g" ]; then
            printf '%s\n' "$temp_dir"
            return 0
        fi
        assert_eq "install -g @anthropic-ai/claude-code --registry=https://registry.npmmirror.com" "$*" "Claude Code npm install arguments"
        printf '%s\n' "$*" > "$args_path"
        printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$TEST_CLAUDE_CLI_PATH"
        chmod +x "$TEST_CLAUDE_CLI_PATH"
    }

    setup_claude_code || fail "missing Claude Code CLI should install successfully"
    assert_eq "install -g @anthropic-ai/claude-code --registry=https://registry.npmmirror.com" "$(<"$args_path")" "recorded Claude Code npm install arguments"
    assert_eq "$TEST_CLAUDE_CLI_PATH" "$CLAUDE_CODE_PATH" "installed Claude Code path"
    EXPECTED_CLAUDE_CODE_PATH="$TEST_CLAUDE_CLI_PATH" bash -c '[ "$CLAUDE_CODE_PATH" = "$EXPECTED_CLAUDE_CODE_PATH" ]' || fail "installed Claude Code path should be exported"
)

test_claude_code_install_fails_without_resolved_cli() (
    local temp_dir
    temp_dir="$(mktemp -d)"

    export PATH="/usr/bin:/bin"
    unset CLAUDE_CODE_PATH
    confirm_tool_install() { return 0; }
    npm() {
        if [ "$1" = "prefix" ] && [ "$2" = "-g" ]; then
            printf '%s\n' "$temp_dir"
            return 0
        fi
        return 0
    }

    if setup_claude_code; then
        fail "setup should fail when Claude Code is still unavailable after npm install"
    fi
)

test_load_rust_environment() (
  local temp_cargo_home original_path
  temp_cargo_home="$(mktemp -d)"
  original_path="$PATH"
  mkdir -p "${temp_cargo_home}/bin"
  printf '%s\n' 'export TEST_RUST_ENV_LOADED=1' > "${temp_cargo_home}/env"

  export CARGO_HOME="$temp_cargo_home"
  unset TEST_RUST_ENV_LOADED
  PATH="$original_path"
  load_rust_environment

  assert_eq "1" "${TEST_RUST_ENV_LOADED:-}" "Rust env file loaded"
  case ":${PATH}:" in
    *":${temp_cargo_home}/bin:"*) ;;
    *) fail "Cargo bin missing from PATH after loading Rust environment" ;;
  esac
)

test_detect_shell_profile_uses_login_shell() (
  local temp_home
  temp_home="$(mktemp -d)"
  HOME="$temp_home"

  SHELL="/bin/zsh"
  assert_eq "${temp_home}/.zshrc" "$(detect_shell_profile)" "zsh profile"

  SHELL="/bin/bash"
  : > "${temp_home}/.bashrc"
  assert_eq "${temp_home}/.bashrc" "$(detect_shell_profile)" "bash profile"
)

test_shell_reload_hint_uses_detected_profile() (
  local temp_home output
  temp_home="$(mktemp -d)"
  HOME="$temp_home"
  SHELL="/bin/zsh"
  : > "${temp_home}/.zshrc"
  log_warn() { printf '[WARN] %s\n' "$*"; }

  _toolchain_require_shell_reload
  output="$(_toolchain_print_shell_reload_hint)"

  assert_contains "cannot reload its parent shell" "$output" "parent shell explanation"
  assert_contains "source \"${temp_home}/.zshrc\"" "$output" "shell reload command"
)

test_default_rust_toolchain
test_existing_required_rust_skips_install
test_existing_rustup_installs_missing_required_rust
test_fresh_rust_install_uses_required_release
test_required_rust_install_failure_is_reported
test_wrong_rust_cannot_be_accepted_after_declining_install
test_bcs_check_only_validates_required_rust_without_installing
test_singlebox_prereqs_do_not_install_missing_rust
test_command_package_mapping
test_library_package_mapping
test_manual_install_hints
test_basic_build_environment_on_current_host
test_failed_install_prints_manual_command
test_claude_code_existing_cli_skips_install
test_claude_code_default_response_installs_missing_cli
test_claude_code_decline_skips_install
test_toolchain_setup_continues_after_claude_code_skip
test_claude_code_installs_missing_cli
test_claude_code_install_fails_without_resolved_cli
test_load_rust_environment
test_detect_shell_profile_uses_login_shell
test_shell_reload_hint_uses_detected_profile

printf 'PASS: singlebox toolchain tests\n'
