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
  export BAAS_APP_DIR="${ROOT}/src/baas"
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

test_backend_default_readiness_window_covers_cold_start() (
  setup_env
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/backend.sh"

  local attempts=0
  unset BACKEND_READY_ATTEMPTS
  backend_ready() {
    attempts=$((attempts + 1))
    [ "$attempts" -ge 120 ]
  }
  backend_stop() { fail "backend should not stop during a normal cold start"; }
  sleep() { :; }

  backend_wait_until_ready
  assert_eq "120" "$attempts" "default backend readiness attempts"
)

test_frontend_start_prepares_dependencies_before_launch() (
  setup_env
  export FRONTEND_DIR="$(mktemp -d)"
  export FRONTEND_LOG="${LOG_DIR}/frontend.log"
  export FRONTEND_PID_FILE="${DEP_DIR}/frontend.pid"
  export FRONTEND_PORT=8000
  export BCS_PORT=21000
  export BCSFUSE_PORT=8765
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/frontend.sh"

  local events_file result
  events_file="$(mktemp)"
  frontend_setup() {
    printf '%s\n' "setup:frontend" >> "$events_file"
    return 23
  }
  stop_port_processes_if_owned() {
    printf '%s\n' "stop:frontend" >> "$events_file"
  }

  if frontend_start; then
    result=0
  else
    result=$?
  fi

  assert_eq "1" "$result" "frontend_start should fail when dependency setup fails"
  assert_eq "setup:frontend" "$(cat "$events_file")" \
    "frontend_start should prepare dependencies before touching the running service"
)

test_frontend_deps_require_dev_commands() (
  setup_env
  export FRONTEND_DIR="$(mktemp -d)"
  export FRONTEND_PORT=8000
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/frontend.sh"

  local package
  mkdir -p "${FRONTEND_DIR}/node_modules/.bin"
  printf '%s\n' '{"name":"frontend-test"}' > "${FRONTEND_DIR}/package.json"
  for package in adapters core ui; do
    mkdir -p "${FRONTEND_DIR}/node_modules/@aix-chat/${package}"
    printf '%s\n' "{\"name\":\"@aix-chat/${package}\"}" \
      > "${FRONTEND_DIR}/node_modules/@aix-chat/${package}/package.json"
  done
  touch "${FRONTEND_DIR}/node_modules/.package-lock.json"

  if frontend_deps_ready; then
    fail "frontend dependencies without cross-env/max should be treated as incomplete"
  fi

  printf '%s\n' '#!/usr/bin/env sh' 'exit 0' > "${FRONTEND_DIR}/node_modules/.bin/cross-env"
  printf '%s\n' '#!/usr/bin/env sh' 'exit 0' > "${FRONTEND_DIR}/node_modules/.bin/max"
  chmod +x \
    "${FRONTEND_DIR}/node_modules/.bin/cross-env" \
    "${FRONTEND_DIR}/node_modules/.bin/max"

  frontend_deps_ready || fail "complete frontend dependencies should be reusable"
)

test_frontend_install_includes_dev_dependencies() (
  setup_env
  export FRONTEND_DIR="$(mktemp -d)"
  export NPM_REGISTRY_URL="https://registry.npmjs.org"
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/frontend.sh"

  local npm_args
  npm_args="$(mktemp)"
  touch "${FRONTEND_DIR}/package-lock.json"
  npm() { printf '%s\n' "$*" > "$npm_args"; }

  install_frontend_deps || fail "mock frontend install should succeed"
  case "$(cat "$npm_args")" in
    *"ci --include=dev "*) ;;
    *) fail "frontend install should explicitly include devDependencies" ;;
  esac
)

# npm's legacy audit endpoint is being retired and the call now stalls ~7m
# before giving up — a fixed cost per invocation, not proportional to the tree
# (33 packages: 421s with audit, 3s without, identical node_modules). Every
# install in the singlebox path must opt out, so a new call site cannot
# silently reintroduce the stall. Global installs (-g) do not audit.
test_npm_installs_opt_out_of_the_audit_call() {
    local offenders
    offenders="$(
        grep -nE '(^|[^[:alnum:]_./-])npm[[:space:]]+(ci|install)([[:space:]]|$)' \
            "${ROOT}"/scripts/modules/*.sh \
        | grep -vE 'log_(info|warn|error)' \
        | grep -vE '^[^:]*:[0-9]+:[[:space:]]*#' \
        | grep -vE 'npm[[:space:]]+install[[:space:]]+-g' \
        | grep -v -- '--no-audit' || true
    )"
    [ -z "$offenders" ] || fail "npm install/ci without --no-audit: ${offenders}"
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

test_baas_start_exports_environment_before_invoking_shell_wrapper() {
  local start_body
  start_body="$(
    sed -n '/^baas_start()/,/^baas_stop()/p' "${ROOT}/scripts/modules/baas.sh"
  )"
  grep -F 'export "${baas_env_args[@]}"' <<<"$start_body" >/dev/null || \
    fail "baas_start must export BAAS environment in its shell before invoking start_in_detached_session"
  if grep -F 'env "${baas_env_args[@]}"' <<<"$start_body" >/dev/null; then
    fail "baas_start must not use env to invoke the start_in_detached_session shell function"
  fi
}

test_baas_bot_cleanup_preserves_bcs_sessions() {
  setup_env
  export RUNTIME_DATA_DIR="$(mktemp -d)"
  export LOCAL_BOTS_DIR="$(mktemp -d)"
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/baas.sh"

  local session_file unrelated_file backup_dir mode
  session_file="${LOCAL_BOTS_DIR}/staff_mock-user/default/openclaw/.bcs/session.json"
  unrelated_file="${LOCAL_BOTS_DIR}/staff_mock-user/default/openclaw/runtime.tmp"
  backup_dir="${RUNTIME_DATA_DIR}/.baas-bcs-sessions"
  mkdir -p "$(dirname "$session_file")"
  printf '%s\n' '{"bot_uuid":"test-bot","token":"test-token"}' > "$session_file"
  printf '%s\n' 'remove me' > "$unrelated_file"
  chmod 600 "$session_file"

  baas_backup_bcs_sessions "$backup_dir"
  rm -rf "${LOCAL_BOTS_DIR:?}/"*
  baas_restore_bcs_sessions "$backup_dir"

  [ -f "$session_file" ] || fail "BAAS cleanup should restore BCS session files"
  assert_eq '{"bot_uuid":"test-bot","token":"test-token"}' "$(cat "$session_file")" \
    "restored BCS session content"
  [ ! -e "$unrelated_file" ] || fail "BAAS cleanup should not restore unrelated bot files"
  [ ! -e "$backup_dir" ] || fail "BAAS cleanup should remove the temporary session backup"

  if stat -f '%Lp' "$session_file" >/dev/null 2>&1; then
    mode="$(stat -f '%Lp' "$session_file")"
  else
    mode="$(stat -c '%a' "$session_file")"
  fi
  assert_eq "600" "$mode" "restored BCS session permissions"
}

test_baas_session_backup_normalizes_trailing_slashes() {
  setup_env
  export RUNTIME_DATA_DIR="$(mktemp -d)"
  local bots_dir backup_dir session_file expected_backup
  bots_dir="$(mktemp -d)"
  backup_dir="${RUNTIME_DATA_DIR}/.baas-bcs-sessions"
  export LOCAL_BOTS_DIR="${bots_dir}///"
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/baas.sh"

  session_file="${bots_dir}/staff_mock-user/default/openclaw/.bcs/session.json"
  expected_backup="${backup_dir}/staff_mock-user/default/openclaw/.bcs/session.json"
  mkdir -p "$(dirname "$session_file")"
  printf '%s\n' '{"bot_uuid":"test-bot"}' > "$session_file"

  baas_backup_bcs_sessions "${backup_dir}///"

  [ -f "$expected_backup" ] || \
    fail "BAAS backup should normalize trailing slashes in bots and backup paths"
}

test_baas_session_restore_normalizes_trailing_slashes() {
  setup_env
  export RUNTIME_DATA_DIR="$(mktemp -d)"
  local bots_dir backup_dir backup_session expected_session
  bots_dir="$(mktemp -d)"
  backup_dir="${RUNTIME_DATA_DIR}/.baas-bcs-sessions"
  export LOCAL_BOTS_DIR="${bots_dir}///"
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/baas.sh"

  backup_session="${backup_dir}/staff_mock-user/default/openclaw/.bcs/session.json"
  expected_session="${bots_dir}/staff_mock-user/default/openclaw/.bcs/session.json"
  mkdir -p "$(dirname "$backup_session")"
  printf '%s\n' '{"bot_uuid":"test-bot"}' > "$backup_session"

  baas_restore_bcs_sessions "${backup_dir}///"

  [ -f "$expected_session" ] || \
    fail "BAAS restore should normalize trailing slashes in bots and backup paths"
}

test_baas_session_backup_refuses_to_overwrite_stale_backup() {
  setup_env
  export RUNTIME_DATA_DIR="$(mktemp -d)"
  export LOCAL_BOTS_DIR="$(mktemp -d)"
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/baas.sh"

  local backup_dir stale_session current_session result
  backup_dir="${RUNTIME_DATA_DIR}/.baas-bcs-sessions"
  stale_session="${backup_dir}/staff_mock-user/stale/openclaw/.bcs/session.json"
  current_session="${LOCAL_BOTS_DIR}/staff_mock-user/current/openclaw/.bcs/session.json"
  mkdir -p "$(dirname "$stale_session")" "$(dirname "$current_session")"
  printf '%s\n' '{"bot_uuid":"stale-bot"}' > "$stale_session"
  printf '%s\n' '{"bot_uuid":"current-bot"}' > "$current_session"

  if baas_backup_bcs_sessions "$backup_dir"; then
    result=0
  else
    result=$?
  fi

  [ "$result" -ne 0 ] || fail "BAAS backup should refuse to overwrite stale recovery data"
  assert_eq '{"bot_uuid":"stale-bot"}' "$(cat "$stale_session")" \
    "stale BCS backup content"
}

test_baas_session_backup_recovers_stale_backup_before_snapshot() {
  setup_env
  export RUNTIME_DATA_DIR="$(mktemp -d)"
  export LOCAL_BOTS_DIR="$(mktemp -d)"
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/baas.sh"

  local backup_dir stale_session current_session
  backup_dir="${RUNTIME_DATA_DIR}/.baas-bcs-sessions"
  stale_session="${backup_dir}/staff_mock-user/stale/openclaw/.bcs/session.json"
  current_session="${LOCAL_BOTS_DIR}/staff_mock-user/current/openclaw/.bcs/session.json"
  mkdir -p "$(dirname "$stale_session")" "$(dirname "$current_session")"
  printf '%s\n' '{"bot_uuid":"stale-bot"}' > "$stale_session"
  printf '%s\n' '{"bot_uuid":"current-bot"}' > "$current_session"

  baas_prepare_bcs_session_backup "$backup_dir"

  [ -f "${backup_dir}/staff_mock-user/stale/openclaw/.bcs/session.json" ] || \
    fail "recovered stale session should be included in the fresh backup"
  [ -f "${backup_dir}/staff_mock-user/current/openclaw/.bcs/session.json" ] || \
    fail "current session should be included in the fresh backup"
}

test_baas_session_scan_failure_keeps_source_and_backup() {
  setup_env
  export RUNTIME_DATA_DIR="$(mktemp -d)"
  export LOCAL_BOTS_DIR="$(mktemp -d)"
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/baas.sh"

  local backup_dir source_session backup_session backup_result restore_result
  backup_dir="${RUNTIME_DATA_DIR}/.baas-bcs-sessions"
  source_session="${LOCAL_BOTS_DIR}/staff_mock-user/default/openclaw/.bcs/session.json"
  backup_session="${backup_dir}/staff_mock-user/default/openclaw/.bcs/session.json"
  mkdir -p "$(dirname "$source_session")"
  printf '%s\n' '{"bot_uuid":"source-bot"}' > "$source_session"

  find() { return 23; }
  if baas_backup_bcs_sessions "$backup_dir"; then
    backup_result=0
  else
    backup_result=$?
  fi
  unset -f find

  [ "$backup_result" -ne 0 ] || fail "BAAS backup should fail when session scan fails"
  [ -f "$source_session" ] || fail "failed backup must keep source sessions"

  mkdir -p "$(dirname "$backup_session")"
  printf '%s\n' '{"bot_uuid":"backup-bot"}' > "$backup_session"
  find() { return 23; }
  if baas_restore_bcs_sessions "$backup_dir"; then
    restore_result=0
  else
    restore_result=$?
  fi
  unset -f find

  [ "$restore_result" -ne 0 ] || fail "BAAS restore should fail when session scan fails"
  [ -f "$backup_session" ] || fail "failed restore must keep recovery data"
}

test_baas_start_refuses_root_bots_dir() (
  setup_env
  export RUNTIME_DATA_DIR="$(mktemp -d)"
  export LOCAL_AIDESKTOP_DIR="$(mktemp -d)"
  export LOCAL_BOTS_DIR="////"
  export LOCAL_MODE=true
  export CHAT_ENGINE="openclaw"
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/baas.sh"

  local events_file plugin_dir result
  events_file="$(mktemp)"
  plugin_dir="$(mktemp -d)"
  mkdir -p "${plugin_dir}/dist/esm"
  : > "${plugin_dir}/dist/esm/index.js"
  stop_port_processes_if_owned() { return 0; }
  stop_matching_processes_if_owned() { return 0; }
  require_port_available_after_owned_stop() { return 0; }
  check_directory_exists() { return 0; }
  setup_bcn_plugin() { return 0; }
  bots_bcn_plugin_load_dir() { printf '%s\n' "$plugin_dir"; }
  baas_prepare_bcs_session_backup() { return 0; }
  baas_restore_bcs_sessions() { return 0; }
  rm() { printf 'rm %s\n' "$*" >> "$events_file"; }
  env() { return 0; }
  set -f

  if baas_start; then
    result=0
  else
    result=$?
  fi

  [ "$result" -ne 0 ] || fail "baas_start should reject a root LOCAL_BOTS_DIR"
  [ ! -s "$events_file" ] || fail "baas_start must reject root before calling rm"
)

test_baas_start_passes_bcn_runtime_configuration() (
  setup_env
  export RUNTIME_DATA_DIR="$(mktemp -d)"
  export LOCAL_AIDESKTOP_DIR="$(mktemp -d)"
  export LOCAL_MODE=false
  export CHAT_ENGINE="openclaw"
  export BCS_PORT="21099"
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/baas.sh"

  local plugin_dir captured_env sequence_file
  plugin_dir="$(mktemp -d)"
  mkdir -p "${plugin_dir}/dist/esm"
  : > "${plugin_dir}/dist/esm/index.js"
  captured_env="$(mktemp)"
  sequence_file="$(mktemp)"

  stop_port_processes_if_owned() { return 0; }
  stop_matching_processes_if_owned() { return 0; }
  require_port_available_after_owned_stop() { return 0; }
  check_directory_exists() { return 0; }
  setup_bcn_plugin() { printf '%s\n' "setup" >> "$sequence_file"; }
  bots_bcn_plugin_load_dir() { printf '%s\n' "resolve" >> "$sequence_file"; printf '%s\n' "$plugin_dir"; }
  start_in_detached_session() {
    printf '%s\n' "start" >> "$sequence_file"
    {
      printf '%s\n' "BCN_PLUGIN_PATH=${BCN_PLUGIN_PATH:-}"
      printf '%s\n' "BCS_PORT=${BCS_PORT:-}"
    } > "$captured_env"
  }

  baas_start

  grep -F "BCN_PLUGIN_PATH=${plugin_dir}" "$captured_env" >/dev/null || \
    fail "baas_start must pass the built BCN plugin path to BAAS"
  grep -F "BCS_PORT=21099" "$captured_env" >/dev/null || \
    fail "baas_start must pass the selected BCS port to BAAS"
  assert_eq $'setup\nresolve\nstart' "$(cat "$sequence_file")" \
    "baas_start must prepare the BCN plugin before launching BAAS"
)

test_baas_start_aborts_when_bcn_plugin_setup_fails() (
  setup_env
  export RUNTIME_DATA_DIR="$(mktemp -d)"
  export LOCAL_AIDESKTOP_DIR="$(mktemp -d)"
  export LOCAL_MODE=false
  export CHAT_ENGINE="openclaw"
  export BCS_PORT="21000"
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/baas.sh"

  local started=false
  stop_port_processes_if_owned() { return 0; }
  stop_matching_processes_if_owned() { return 0; }
  require_port_available_after_owned_stop() { return 0; }
  check_directory_exists() { return 0; }
  setup_bcn_plugin() { return 1; }
  env() { started=true; return 0; }

  if baas_start; then
    fail "baas_start must fail when BCN plugin setup fails"
  fi
  [ "$started" = false ] || fail "BAAS must not launch after BCN plugin setup failure"
)

test_5bot_openclaw_config_is_written_private() {
  grep -F 'umask 077' "${ROOT}/src/bcs/scripts/start_bcs_bots.sh" >/dev/null || \
    fail "5bot openclaw config should be written under umask 077"
  grep -F 'chmod 600 "$config_file"' "${ROOT}/src/bcs/scripts/start_bcs_bots.sh" >/dev/null || \
    fail "5bot openclaw config should be chmod 600"
}

test_local_bcs_launchers_supply_required_signing_keys() {
  local singlebox_start five_bot_start group_secret_default principal_secret_default
  singlebox_start="$(sed -n '/^start_bcs_binary()/,/^}/p' "${ROOT}/scripts/modules/bcs.sh")"
  five_bot_start="$(sed -n '/^start_bcs()/,/^}/p' "${ROOT}/src/bcs/scripts/start_bcs_bots.sh")"
  group_secret_default='export BCS_SECRET_BCN_GROUP_SESSION_WS_JWT="${BCS_SECRET_BCN_GROUP_SESSION_WS_JWT:-local-only-bcn-group-session-ws-jwt-signing-key}"'
  principal_secret_default='export AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE="${AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE:-avernet-dev-signing-key-NOT-FOR-PROD}"'

  grep -F 'if [ "${BCS_SERVER_ENV}" = "local" ]; then' <<<"$singlebox_start" >/dev/null || \
    fail "singlebox BCS launcher must scope the group-session key default to local mode"
  grep -F "$group_secret_default" <<<"$singlebox_start" >/dev/null || \
    fail "singlebox BCS launcher must provide an overridable local group-session key"
  grep -F "$principal_secret_default" <<<"$singlebox_start" >/dev/null || \
    fail "singlebox BCS launcher must explicitly provide an overridable local Principal key"
  grep -F 'if [ "$SERVER_ENV" = "local" ]; then' <<<"$five_bot_start" >/dev/null || \
    fail "5bot BCS launcher must scope the group-session key default to local mode"
  grep -F "$group_secret_default" <<<"$five_bot_start" >/dev/null || \
    fail "5bot BCS launcher must provide an overridable local group-session key"
  grep -F "$principal_secret_default" <<<"$five_bot_start" >/dev/null || \
    fail "5bot BCS launcher must explicitly provide an overridable local Principal key"
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
    "${ROOT}/src/baas/scripts/app.sh" >/dev/null; then
    fail "singlebox BAAS should read its workspace folder from config"
  fi
  grep -F 'env_folder: "aidesktop_singlebox"' \
    "${ROOT}/src/baas/singlebox-configs/application-dev.yaml" >/dev/null || \
    fail "singlebox BAAS overlay should preserve the isolated workspace folder"
  if grep -F 'SERVER_ENV=singlebox' <<<"$start_body" >/dev/null; then
    fail "backend startup must not use singlebox as a data Env"
  fi
}

test_dynamic_bot_gateway_receives_manual_model_credential() (
  setup_env
  export BCS_PORT="21000"
  export SINGLEBOX_MODEL_CONFIG_MODE="manual"
  OPENCLAW_OPENAI_API_KEY="test-model-key"
  export -n OPENCLAW_OPENAI_API_KEY
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/bots.sh"

  local attempt
  export TEST_DYNAMIC_PROFILE_DIR="$(mktemp -d)"
  export TEST_DYNAMIC_WORKSPACE_DIR="$(mktemp -d)"
  export TEST_DYNAMIC_CAPTURED_ENV="$(mktemp)"

  bcs_bot_profile_dir() { printf '%s\n' "$TEST_DYNAMIC_PROFILE_DIR"; }
  bots_dynamic_workspace_dir() { printf '%s\n' "$TEST_DYNAMIC_WORKSPACE_DIR"; }
  bcs_cli_path() { printf '%s\n' /usr/bin/true; }
  lsof() { return 1; }
  port_is_listening() { return 0; }
  start_in_detached_session() {
    env | awk -F= '$1 == "OPENCLAW_OPENAI_API_KEY" { print $0 }' > "$TEST_DYNAMIC_CAPTURED_ENV"
  }

  bots_dynamic_start_openclaw "test-bot" "test-profile" "30999" \
    "${LOG_DIR}/test-bot.log" "test-source"

  for attempt in $(seq 1 20); do
    [ -s "$TEST_DYNAMIC_CAPTURED_ENV" ] && break
    sleep 0.05
  done
  assert_eq "OPENCLAW_OPENAI_API_KEY=test-model-key" "$(cat "$TEST_DYNAMIC_CAPTURED_ENV")" \
    "dynamic gateway manual model credential"
)

test_all_start_rolls_back_started_services_on_failure
test_backend_health_failure_stops_backend
test_backend_wait_fails_when_started_process_exits
test_backend_default_readiness_window_covers_cold_start
test_frontend_start_prepares_dependencies_before_launch
test_frontend_deps_require_dev_commands
test_frontend_install_includes_dev_dependencies
test_npm_installs_opt_out_of_the_audit_call
test_service_modules_use_ownership_aware_stop_helpers
test_service_starts_fail_when_ports_remain_occupied
test_baas_stop_does_not_delegate_to_app_stop
test_baas_start_exports_environment_before_invoking_shell_wrapper
test_baas_bot_cleanup_preserves_bcs_sessions
test_baas_session_backup_normalizes_trailing_slashes
test_baas_session_restore_normalizes_trailing_slashes
test_baas_session_backup_refuses_to_overwrite_stale_backup
test_baas_session_backup_recovers_stale_backup_before_snapshot
test_baas_session_scan_failure_keeps_source_and_backup
test_baas_start_refuses_root_bots_dir
test_baas_start_passes_bcn_runtime_configuration
test_baas_start_aborts_when_bcn_plugin_setup_fails
test_5bot_openclaw_config_is_written_private
test_local_bcs_launchers_supply_required_signing_keys
test_ready_banner_describes_full_stack
test_backend_separates_profile_env_and_workspace_folder
test_dynamic_bot_gateway_receives_manual_model_credential

printf 'PASS: singlebox service guard tests\n'
