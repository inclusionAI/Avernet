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

setup_env() {
  export PROJECT_ROOT="$ROOT"
  export SCRIPT_DIR="${ROOT}/scripts"
  export DEP_DIR="$(mktemp -d)"
  export LOG_DIR="${DEP_DIR}/logs"
  export BACKEND_DIR="${ROOT}/src/backend"
  export BAAS_APP_DIR="${ROOT}/src/baas/packages/community"
  export ENGINE_DIR="${ROOT}/src/engine"
  mkdir -p "$LOG_DIR"

  log_info() { printf '[INFO] %s\n' "$*"; }
  log_warn() { printf '[WARN] %s\n' "$*"; }
  log_error() { printf '[ERROR] %s\n' "$*" >&2; }
}

test_all_start_rolls_back_started_services_on_failure() {
  setup_env
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/all.sh"

  local events_file
  events_file="$(mktemp)"
  check_prereqs_for_services() { return 0; }
  print_local_stack_ready_banner() { return 0; }

  baas_start() { printf '%s\n' "start:baas" >> "$events_file"; }
  backend_start() { printf '%s\n' "start:backend" >> "$events_file"; }
  bcs_start() { printf '%s\n' "start:bcs" >> "$events_file"; }
  bots_start() { printf '%s\n' "start:bots" >> "$events_file"; }
  demo_bot_start() { printf '%s\n' "start:demo_bot" >> "$events_file"; return 23; }
  frontend_start() { printf '%s\n' "start:frontend" >> "$events_file"; }

  baas_stop() { printf '%s\n' "stop:baas" >> "$events_file"; }
  backend_stop() { printf '%s\n' "stop:backend" >> "$events_file"; }
  bcs_stop() { printf '%s\n' "stop:bcs" >> "$events_file"; }
  bots_stop() { printf '%s\n' "stop:bots" >> "$events_file"; }
  demo_bot_stop() { printf '%s\n' "stop:demo_bot" >> "$events_file"; }
  frontend_stop() { printf '%s\n' "stop:frontend" >> "$events_file"; }

  if all_start; then
    fail "all_start should fail when demo_bot_start fails"
  fi

  assert_eq $'start:baas\nstart:backend\nstart:bcs\nstart:bots\nstart:demo_bot\nstop:bots\nstop:bcs\nstop:backend\nstop:baas' \
    "$(cat "$events_file")" \
    "all_start rollback order"
}

test_backend_health_failure_stops_backend() {
  setup_env
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/backend.sh"

  local events_file
  events_file="$(mktemp)"
  BACKEND_READY_ATTEMPTS=1
  BACKEND_LOG="${LOG_DIR}/backend.log"
  printf '%s\n' "backend failed to bind" > "$BACKEND_LOG"
  backend_ready() { return 1; }
  backend_stop() { printf '%s\n' "backend_stop" >> "$events_file"; }

  if backend_wait_until_ready; then
    fail "backend_wait_until_ready should fail when health never becomes ready"
  fi
  assert_eq "backend_stop" "$(cat "$events_file")" "backend health failure should stop backend"
}

test_backend_wait_fails_when_started_process_exits() {
  setup_env
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/backend.sh"

  local events_file pid
  events_file="$(mktemp)"
  BACKEND_LOG="${LOG_DIR}/backend.log"
  printf '%s\n' "backend exited" > "$BACKEND_LOG"
  backend_ready() { return 0; }
  backend_stop() { printf '%s\n' "backend_stop" >> "$events_file"; }
  ( exit 0 ) &
  pid=$!
  wait "$pid" || true

  if backend_wait_until_ready "$pid"; then
    fail "backend_wait_until_ready should fail when the started process exits"
  fi
  assert_eq "backend_stop" "$(cat "$events_file")" "exited backend process should trigger cleanup"
}

test_service_modules_use_ownership_aware_stop_helpers() {
  local offenders
  offenders="$(
    grep -nE 'kill_port_process|kill_process_by_path' \
      "${ROOT}/scripts/modules/backend.sh" \
      "${ROOT}/scripts/modules/baas.sh" \
      "${ROOT}/scripts/modules/engine.sh" || true
  )"
  [ -z "$offenders" ] || fail "service modules still use unsafe kill helpers: ${offenders}"
}

test_5bot_openclaw_config_is_written_private() {
  grep -F 'umask 077' "${ROOT}/src/bcs/scripts/start_bcs_bots.sh" >/dev/null || \
    fail "5bot openclaw config should be written under umask 077"
  grep -F 'chmod 600 "$config_file"' "${ROOT}/src/bcs/scripts/start_bcs_bots.sh" >/dev/null || \
    fail "5bot openclaw config should be chmod 600"
}

test_ready_banner_describes_full_stack() {
  grep -F 'FULL SINGLEBOX STACK' "${ROOT}/scripts/utils.sh" >/dev/null || \
    fail "ready banner should describe full singlebox stack"
  grep -F 'BAAS BACKEND BCS' "${ROOT}/scripts/utils.sh" >/dev/null || \
    fail "ready banner should include backend services"
  grep -F '5BOTS DEMO FRONTEND' "${ROOT}/scripts/utils.sh" >/dev/null || \
    fail "ready banner should include demo bot and frontend"
}

test_all_start_rolls_back_started_services_on_failure
test_backend_health_failure_stops_backend
test_backend_wait_fails_when_started_process_exits
test_service_modules_use_ownership_aware_stop_helpers
test_5bot_openclaw_config_is_written_private
test_ready_banner_describes_full_stack

printf 'PASS: singlebox service guard tests\n'
