#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE="${ROOT}/scripts/fixtures/gateway_env_capture.sh"
TEST_ROOT="$(mktemp -d)"

cleanup() {
  local status=$?
  rm -rf "${TEST_ROOT}"
  exit "${status}"
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_capture() {
  local expected_env="$1"
  local expected
  expected="$(printf 'start:%s\nstop:%s\n' "${expected_env}" "${expected_env}")"
  [[ "$(cat "${CAPTURE_FILE}")" == "${expected%$'\n'}" ]] || fail "Gateway should receive ${expected_env}, got $(tr '\n' ',' < "${CAPTURE_FILE}")"
}

export LOG_DIR="${TEST_ROOT}/logs"
export DEP_DIR="${TEST_ROOT}/dependencies"
export GATEWAY_DIR="${ROOT}/src/gateway"
export GATEWAY_PORT="18889"
export CAPTURE_FILE="${TEST_ROOT}/gateway-env.txt"
mkdir -p "${LOG_DIR}" "${DEP_DIR}"

source "${ROOT}/scripts/modules/gateway.sh"

GATEWAY_APP_SCRIPT="${TEST_ROOT}/gateway-env-capture.sh"
cp "${FIXTURE}" "${GATEWAY_APP_SCRIPT}"
chmod +x "${GATEWAY_APP_SCRIPT}"

log_info() { :; }
log_error() { :; }
gateway_setup() { :; }
check_directory_exists() { [[ -d "$1" ]]; }
stop_port_processes_if_owned() { :; }
stop_matching_processes_if_owned() { :; }
require_port_available_after_owned_stop() { :; }
stop_process_if_owned() { :; }
gateway_ready() { return 0; }

export SERVER_ENV="dev"
GATEWAY_SERVER_ENV="${GATEWAY_SERVER_ENV:-local}"
gateway_start
gateway_stop
assert_capture "local"

: > "${CAPTURE_FILE}"
export GATEWAY_SERVER_ENV="prepub"
gateway_start
gateway_stop
assert_capture "prepub"

printf 'PASS: singlebox gateway environment tests\n'
