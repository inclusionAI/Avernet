#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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

YELLOW=""
NC=""
log_info() { :; }
log_warn() { :; }
log_error() { :; }

# Functions referenced by toolchain.sh but supplied by utils.sh in production.
check_rust_installed() { return 1; }
check_protobuf_installed() { return 1; }

# shellcheck source=/dev/null
source "${ROOT}/scripts/toolchain.sh"

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
}

test_command_package_mapping
test_library_package_mapping
test_manual_install_hints
test_basic_build_environment_on_current_host
test_failed_install_prints_manual_command

printf 'PASS: singlebox toolchain tests\n'
