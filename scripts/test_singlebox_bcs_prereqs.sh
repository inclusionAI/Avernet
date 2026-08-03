#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODULE="${ROOT}/scripts/modules/bcs.sh"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

setup_env() {
  export PROJECT_ROOT="$ROOT"
  export SCRIPT_DIR="${ROOT}/scripts"
  export DEP_DIR="$(mktemp -d)"
  export LOG_DIR="${DEP_DIR}/logs"
  export BCS_DIR="${ROOT}/src/bcs"
  export BCS_PORT="21000"
  export BCS_CONFIG_DIR="${BCS_DIR}/configs"
  export BCS_CONFIG_FILE="${BCS_CONFIG_DIR}/bcs-config-local.toml"
  export BCS_RUNTIME_CONFIG_DIR="${DEP_DIR}/bcs-config"
  export BCS_RUNTIME_CONFIG_FILE="${BCS_RUNTIME_CONFIG_DIR}/bcs-config.toml"
  export BCS_DATA_DIR="${DEP_DIR}/bcs_data"
  export BCS_PID_FILE="${LOG_DIR}/bcs.pid"
  export BCS_LOG="${LOG_DIR}/bcs.log"
  export BCS_PANEL_ASSET_DIR="${BCS_DIR}/bcs-panel"
  export BCS_CLI_BIN="${BCS_DIR}/target/debug/bcs-cli"
  export BCS_ADMIN_BIN="${BCS_DIR}/target/debug/bcs-admin"
  export NC=""
  export CYAN=""
  export BCS_SERVER_ENV=""
  unset BCS_E2E_MOCK_BASE_URL
  mkdir -p "$LOG_DIR"

  log_info() { printf '[INFO] %s\n' "$*"; }
  log_warn() { printf '[WARN] %s\n' "$*"; }
  log_error() { printf '[ERROR] %s\n' "$*" >&2; }
  prereq_ok() { printf 'ok %s\n' "$*"; }
  prereq_warn() { printf 'warn %s\n' "$*"; }
  prereq_error() { printf 'error %s\n' "$*"; }
  check_command() { command -v "$1" >/dev/null 2>&1; }
}

assert_bcsfuse_config_value() {
  local config_file="$1"
  local expected="$2"
  awk '
    /^\[bcsfuse\]$/ { in_section = 1; next }
    in_section && /^\[/ { exit }
    in_section { print }
  ' "$config_file" | grep -Fx "$expected" >/dev/null || \
    fail "missing '${expected}' in ${config_file}"
}

test_cargo_hint_when_rustup_cargo_exists_outside_path() {
  setup_env
  temp_home="$(mktemp -d)"
  mkdir -p "${temp_home}/.cargo/bin"
  : > "${temp_home}/.cargo/bin/cargo"
  chmod +x "${temp_home}/.cargo/bin/cargo"
  HOME="$temp_home"

  # shellcheck source=/dev/null
  source "$MODULE"

  message="$(bcs_cargo_not_found_message)"
  grep -F "${temp_home}/.cargo/bin/cargo" <<<"$message" >/dev/null || fail "cargo path missing from hint"
  grep -F "source \"${temp_home}/.cargo/env\"" <<<"$message" >/dev/null || fail "source cargo env hint missing"
}

test_cargo_hint_when_cargo_is_not_installed() {
  setup_env
  temp_home="$(mktemp -d)"
  HOME="$temp_home"

  # shellcheck source=/dev/null
  source "$MODULE"

  message="$(bcs_cargo_not_found_message)"
  grep -F "Install:" <<<"$message" >/dev/null || fail "install hint missing"
  grep -F "rustup.rs" <<<"$message" >/dev/null || fail "rustup hint missing"
}

test_default_runtime_config_keeps_bcsfuse_settings() {
  setup_env
  export BCS_CONFIG_DIR="$BCS_RUNTIME_CONFIG_DIR"

  # shellcheck source=/dev/null
  source "$MODULE"
  prepare_bcs_runtime_config >/dev/null

  assert_bcsfuse_config_value "$BCS_RUNTIME_CONFIG_FILE" "sync_timeout_ms = 10000"
  assert_bcsfuse_config_value "$BCS_RUNTIME_CONFIG_FILE" "sync_max_attempts = 3"
  assert_bcsfuse_config_value "$BCS_RUNTIME_CONFIG_FILE" "sync_retry_base_delay_ms = 1000"
}

test_e2e_and_coverage_runtime_configs_use_fast_bcsfuse_settings() {
  setup_env
  export BCS_E2E_MOCK_BASE_URL="http://127.0.0.1:39090"

  # shellcheck source=/dev/null
  source "$MODULE"

  local runtime_config_dir config_file
  for runtime_config_dir in \
    "${DEP_DIR}/standalone/bcs-config" \
    "${DEP_DIR}/coverage/singlebox/standalone-runtime/bcs-config"; do
    export BCS_CONFIG_DIR="$runtime_config_dir"
    prepare_bcs_runtime_config >/dev/null

    for config_file in \
      "${runtime_config_dir}/bcs-config.toml" \
      "${runtime_config_dir}/bcs-config-local.toml"; do
      assert_bcsfuse_config_value "$config_file" "sync_timeout_ms = 1"
      assert_bcsfuse_config_value "$config_file" "sync_max_attempts = 1"
      assert_bcsfuse_config_value "$config_file" "sync_retry_base_delay_ms = 1"
    done
  done
}

test_cargo_hint_when_rustup_cargo_exists_outside_path
test_cargo_hint_when_cargo_is_not_installed
test_default_runtime_config_keeps_bcsfuse_settings
test_e2e_and_coverage_runtime_configs_use_fast_bcsfuse_settings

printf 'PASS: singlebox BCS prereq tests\n'
