#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

setup_env() {
  local temp_dir
  temp_dir="$(mktemp -d)"
  # shellcheck source=/dev/null
  source "${ROOT}/singlebox/modules/bcsfuse.sh"
  BCSFUSE_DIR="${temp_dir}/project"
  BCSFUSE_RUNTIME_DIR="${temp_dir}/runtime"
  BCSFUSE_ENV_FILE="${BCSFUSE_RUNTIME_DIR}/env/.env.local"
  BCSFUSE_PROVIDER_MODE=dev
  PYPI_INDEX_URL="https://packages.example.invalid/simple"
  UV_ARGS_FILE="${temp_dir}/uv-args"
  TEST_UV_STATUS=0
  mkdir -p "$BCSFUSE_DIR"

  log_info() { :; }
  log_warn() { :; }
  log_error() { :; }
  check_directory_exists() { [ -d "$1" ]; }
  check_uv_installed() { return 0; }
  # Capture the real setup arguments without installing packages or using the network.
  uv() {
    printf '%s\n' "$@" > "$UV_ARGS_FILE"
    return "$TEST_UV_STATUS"
  }
}

test_sync_uses_full_project_python_range() (
  setup_env
  bcsfuse_setup || fail "setup should succeed when uv succeeds"

  local expected actual project_range
  project_range="$(sed -n 's/^requires-python = "\([^"]*\)"$/\1/p' "${ROOT}/apps/bcsfuse/pyproject.toml")"
  [ "$project_range" = ">=3.10,<3.13" ] || fail "revisit Python selection test when project support changes"
  expected="$(printf '%s\n' sync --python "$project_range" --index-url "$PYPI_INDEX_URL")"
  actual="$(cat "$UV_ARGS_FILE")"
  [ "$actual" = "$expected" ] || fail "uv sync must pass both project Python bounds, not just <3.13; got: $actual"
  [ -d "${BCSFUSE_RUNTIME_DIR}/logs" ] || fail "successful sync should allow runtime setup"
)

test_sync_failure_stops_setup() (
  setup_env
  TEST_UV_STATUS=1
  if bcsfuse_setup; then
    fail "setup must propagate uv sync failure"
  fi
  [ -f "$UV_ARGS_FILE" ] || fail "uv should have been called"
  [ ! -d "$BCSFUSE_RUNTIME_DIR" ] || fail "failed sync must not create runtime state"
)

test_sync_uses_full_project_python_range
test_sync_failure_stops_setup
printf 'PASS: singlebox BCSFuse Python selection tests\n'
