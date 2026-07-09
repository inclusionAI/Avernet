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
  mkdir -p "$LOG_DIR"

  log_info() { printf '[INFO] %s\n' "$*"; }
  log_warn() { printf '[WARN] %s\n' "$*"; }
  log_error() { printf '[ERROR] %s\n' "$*" >&2; }
  prereq_ok() { printf 'ok %s\n' "$*"; }
  prereq_warn() { printf 'warn %s\n' "$*"; }
  prereq_error() { printf 'error %s\n' "$*"; }
  check_command() { command -v "$1" >/dev/null 2>&1; }
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

test_cargo_hint_when_rustup_cargo_exists_outside_path
test_cargo_hint_when_cargo_is_not_installed

printf 'PASS: singlebox BCS prereq tests\n'
