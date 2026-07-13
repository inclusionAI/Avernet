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

test_service_starts_fail_when_ports_remain_occupied() {
  grep -F 'require_port_available_after_owned_stop 8888 "backend"' "${ROOT}/scripts/modules/backend.sh" >/dev/null || \
    fail "backend_start should fail if port 8888 remains occupied after owned cleanup"
  grep -F 'require_port_available_after_owned_stop 8890 "BAAS"' "${ROOT}/scripts/modules/baas.sh" >/dev/null || \
    fail "baas_start should fail if port 8890 remains occupied after owned cleanup"
  grep -F 'require_port_available_after_owned_stop 20003 "engine"' "${ROOT}/scripts/modules/engine.sh" >/dev/null || \
    fail "engine_start should fail if port 20003 remains occupied after owned cleanup"
}

test_baas_stop_does_not_delegate_to_app_stop() {
  local stop_body
  stop_body="$(
    sed -n '/^baas_stop()/,/^baas_status()/p' "${ROOT}/scripts/modules/baas.sh"
  )"
  if grep -F 'app.sh" stop' <<<"$stop_body" >/dev/null; then
    fail "baas_stop should not call app.sh stop because app.sh has unsafe port kill fallback"
  fi
  grep -F 'stop_process_if_owned' <<<"$stop_body" >/dev/null || \
    fail "baas_stop should stop pidfile process with ownership verification"
  grep -F 'stop_port_processes_if_owned "$port"' <<<"$stop_body" >/dev/null || \
    fail "baas_stop should clean the BAAS port with ownership verification"
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

test_backend_separates_profile_env_and_workspace_folder() {
  local start_body
  start_body="$(sed -n '/^backend_start()/,/^backend_wait_until_ready()/p' "${ROOT}/scripts/modules/backend.sh")"

  grep -F 'SERVER_ENV=dev DEPLOY_PROFILE=singlebox' <<<"$start_body" >/dev/null || \
    fail "singlebox backend should launch with SERVER_ENV=dev and DEPLOY_PROFILE=singlebox"
  if grep -F 'WORKSPACE_ENV_FOLDER=' <<<"$start_body" >/dev/null; then
    fail "singlebox backend should read its workspace folder from the profile overlay"
  fi
  grep -F 'env_folder: "aidesktop_singlebox"' \
    "${ROOT}/src/backend/src/agentclaw/community/configs/application-singlebox.yaml" >/dev/null || \
    fail "singlebox backend overlay should preserve the isolated workspace folder"
  if grep -F 'WORKSPACE_ENV_FOLDER=' \
    "${ROOT}/src/baas/packages/community/scripts/app.sh" >/dev/null; then
    fail "singlebox BAAS should read its workspace folder from config"
  fi
  grep -F 'env_folder: "aidesktop_singlebox"' \
    "${ROOT}/src/baas/packages/community/singlebox-configs/application-dev.yaml" >/dev/null || \
    fail "singlebox BAAS overlay should preserve the isolated workspace folder"
  if grep -F 'SERVER_ENV=singlebox' <<<"$start_body" >/dev/null; then
    fail "backend startup must not use singlebox as a data Env"
  fi
}

test_all_start_rolls_back_started_services_on_failure
test_backend_health_failure_stops_backend
test_backend_wait_fails_when_started_process_exits
test_service_modules_use_ownership_aware_stop_helpers
test_service_starts_fail_when_ports_remain_occupied
test_baas_stop_does_not_delegate_to_app_stop
test_5bot_openclaw_config_is_written_private
test_ready_banner_describes_full_stack
test_backend_separates_profile_env_and_workspace_folder

printf 'PASS: singlebox service guard tests\n'
