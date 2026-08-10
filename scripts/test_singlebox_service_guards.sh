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

test_mixed_all_preflight_rejects_external_port_before_restart() (
  setup_env
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/all.sh"

  claude_bots_enabled() { return 0; }
  lsof() { printf '%s\n' '4242'; }
  process_cwd() { printf '%s\n' '/tmp/another-checkout'; }
  path_is_under_dir() { return 1; }

  if all_mixed_port_ownership_preflight; then
    fail "mixed preflight must reject a listener owned by another checkout"
  fi
)

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

test_frontend_stdin_keeper_survives_singlebox_exit() {
  local source
  source="${ROOT}/scripts/modules/frontend.sh"

  grep -F "nohup bash -c 'tail -f /dev/null | npm run \"\$1\"' bash \"\$frontend_script\"" "$source" >/dev/null || \
    fail "frontend stdin keeper and npm must share the nohup process group"
  if grep -F 'tail -f /dev/null | BCS_PORT=' "$source" >/dev/null; then
    fail "frontend must not leave the stdin keeper outside nohup"
  fi
}

test_frontend_human_sender_uses_workbench_bot_id() {
  local provider_hook group_chat_page user_collab_tab use_group_chat
  provider_hook="${ROOT}/src/frontend/src/pages/GroupChat/hooks/useGroupChatProviders.ts"
  group_chat_page="${ROOT}/src/frontend/src/pages/GroupChat/components/GroupChatPage.tsx"
  user_collab_tab="${ROOT}/src/frontend/src/pages/GroupChat/components/BottomPanel/UserCollabTab.tsx"
  use_group_chat="${ROOT}/src/frontend/src/pages/GroupChat/hooks/useGroupChat.ts"

  grep -F 'frame.params.bot_id = frame.params.sender_id;' "$provider_hook" >/dev/null || \
    fail "frontend BCS transport must map the authenticated human sender to bot_id"
  grep -F 'sendMessage(msg, mentions, undefined, activeSession?.sessionId, senderId);' "$group_chat_page" >/dev/null || \
    fail "group chat must not reuse a human sender as the bot_uuid argument"
  grep -F 'userId ? `human_${userId}` : undefined,' "$user_collab_tab" >/dev/null || \
    fail "user collaboration sender must retain the authenticated human identity"
  grep -F "console.debug('[useGroupChat] Sending message'" "$use_group_chat" >/dev/null || \
    fail "frontend send diagnostics must be metadata-only"
  if grep -F "console.log('[useGroupChat] Sending message with params:', params);" "$use_group_chat" >/dev/null; then
    fail "frontend send diagnostics must not log chat params containing user content"
  fi
}

test_bcsfuse_status_loads_runtime_before_pid_lookup() {
  local module
  module="${ROOT}/scripts/modules/bcsfuse.sh"
  grep -A8 '^bcsfuse_status()' "$module" | grep -F 'bcsfuse_load_env' >/dev/null || \
    fail "bcsfuse status must resolve standalone runtime paths before reading its pid file"
}

test_service_modules_use_ownership_aware_stop_helpers() {
  local offenders
  offenders="$(
    grep -nE 'kill_port_process|kill_process_by_path' \
      "${ROOT}/scripts/modules/backend.sh" \
      "${ROOT}/scripts/modules/baas.sh" \
      "${ROOT}/scripts/modules/engine.sh" \
      "${ROOT}/scripts/modules/bcsfuse.sh" || true
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
  grep -F 'require_port_available_after_owned_stop "${BCSFUSE_PORT}" "bcsfuse"' "${ROOT}/scripts/modules/bcsfuse.sh" >/dev/null || \
    fail "bcsfuse_start should fail if its port remains occupied after owned cleanup"
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

test_baas_ready_requires_its_health_endpoint() (
  setup_env
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/modules/baas.sh"

  curl() { return 0; }
  baas_ready || fail "baas_ready should pass when the local BAAS health endpoint responds"
  curl() { return 1; }
  if baas_ready; then
    fail "baas_ready must fail when the local BAAS health endpoint is unavailable"
  fi
)

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
  env() { printf '%s\n' "start" >> "$sequence_file"; printf '%s\n' "$*" > "$captured_env"; return 0; }

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
  grep -F '5 OPENCLAW + 3 CLAUDE CODE' "${ROOT}/scripts/utils.sh" >/dev/null || \
    fail "mixed ready banner should identify the complete eight-bot topology"
}

test_frontend_only_banner_does_not_claim_full_stack_ready() {
  grep -F 'FRONTEND SERVICE READY' "${ROOT}/scripts/utils.sh" >/dev/null || \
    fail "frontend-only banner must identify itself as a service readiness hint"
  grep -F 'not verified by this service-only command' "${ROOT}/scripts/utils.sh" >/dev/null || \
    fail "frontend-only banner must not imply backend or BCS readiness"
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

test_restart_all_preflights_before_stopping() {
  local restart_body all_restart_body preflight_line stop_line
  restart_body="$(sed -n '/^        restart)/,/^        clean)/p' "${ROOT}/scripts/singlebox.sh")"
  all_restart_body="$(sed -n '/^all_restart()/,/^all_clean()/p' "${ROOT}/scripts/modules/all.sh")"
  preflight_line="$(grep -n 'all_preflight restart' <<<"$all_restart_body" | head -1 | cut -d: -f1)"
  stop_line="$(grep -n '^    all_stop$' <<<"$all_restart_body" | head -1 | cut -d: -f1)"
  [ -n "$preflight_line" ] && [ -n "$stop_line" ] && [ "$preflight_line" -lt "$stop_line" ] || \
    fail "all_restart must preflight before stopping the current stack"
  grep -F 'restart_service "$svc" || exit 1' <<<"$restart_body" >/dev/null || \
    fail "restart all must dispatch through the all-group lifecycle"
  if grep -F 'stop_service "$svc"' <<<"$restart_body" >/dev/null; then
    fail "top-level restart must not decompose all into individual service stops"
  fi
}

test_bcs_baas_provider_uses_h2c_and_owned_lifecycle() {
  local module stop_body
  module="${ROOT}/scripts/modules/bcs_baas_provider.sh"
  grep -F 'bcs_baas_provider_bridge.mjs' "$module" >/dev/null || \
    fail "Provider bridge module must launch the h2c bridge"
  grep -F 'cd "$PROJECT_ROOT"' "$module" >/dev/null || \
    fail "Provider bridge must start from the current checkout for owned PID cleanup"
  grep -F 'X-Mock-User-Id: ${bcs_owner_id}' "$module" >/dev/null || \
    fail "Provider registration must use the active BCS mock user"
  grep -F 'provider_ref="${bot_id}:${backend_entity_id}"' "$module" >/dev/null || \
    fail "Provider ref must retain the Backend bot owner for BaaS binding lookup"
  grep -F -- '--arg owner "$bcs_owner_id"' "$module" >/dev/null || \
    fail "Provider bot owner must be the active BCS user"
  grep -F '/bots/${bot_uuid}/visibility' "$module" >/dev/null || \
    fail "Provider bot registration must publish its local BCS bot"
  stop_body="$(sed -n '/^bcs_baas_provider_stop()/,/^bcs_baas_provider_ready()/p' "$module")"
  grep -F 'port_is_listening "$BCS_BAAS_PROVIDER_PORT"' <<<"$stop_body" >/dev/null || \
    fail "Provider bridge stop must retain state when a non-owned port listener remains"
  grep -F 'createServer' "${ROOT}/scripts/bcs_baas_provider_bridge.mjs" >/dev/null || \
    fail "Provider bridge must use a native HTTP/2 server"
  if grep -F 'provider_tokens' "${ROOT}/scripts/bcs_baas_provider_bridge.mjs" >/dev/null; then
    fail "Provider bridge must not accept unbound legacy provider tokens"
  fi
}

test_bcs_sse_diagnostics_do_not_log_raw_chat_payloads() {
  local source
  source="${ROOT}/src/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs"
  if grep -F 'sse_data =' "$source" >/dev/null; then
    fail "BCS SSE diagnostics must not log raw SSE data"
  fi
  if grep -F '?other, "unexpected agent data' "$source" >/dev/null; then
    fail "BCS SSE diagnostics must not debug-log opaque agent data"
  fi
  if grep -F 'webhook_url = %webhook_url,' "$source" >/dev/null; then
    fail "BCS Provider diagnostics must redact webhook URL credentials and query values"
  fi
}

test_bcs_bot_diagnostics_do_not_log_raw_frames_or_tokens() {
  local ws_handler bot_core bot_store memory_store
  ws_handler="${ROOT}/src/bcs/crates/adapters/ws/bcs-ws/src/bot/handler.rs"
  bot_core="${ROOT}/src/bcs/crates/services/bcs-bot/src/core/bot_core.rs"
  bot_store="${ROOT}/src/bcs/crates/services/bcs-bot-store/src/lib.rs"
  memory_store="${ROOT}/src/bcs/crates/services/bcs-bot-store/src/memory.rs"

  if grep -F 'text = %text' "$ws_handler" >/dev/null; then
    fail "BCS bot WebSocket diagnostics must not log raw client frames"
  fi
  if grep -E 'token(_preview)? = %' "$bot_core" "$bot_store" "$memory_store" >/dev/null; then
    fail "BCS bot diagnostics must not log token values or previews"
  fi
}

test_claude_relay_diagnostics_do_not_log_token_previews() {
  local router
  router="${ROOT}/src/engine/src/engine/community/claude_code_gateway/src/claude-code-router.ts"
  if grep -E 'ANTHROPIC_AUTH_TOKEN.*substring|token.*substring' "$router" >/dev/null; then
    fail "Claude relay diagnostics must not log token previews"
  fi
}

test_provider_session_context_is_silent_by_default() {
  local source test_source
  source="${ROOT}/src/bcs/crates/services/bcs-system-message/src/producers/session_context.rs"
  test_source="${ROOT}/src/bcs/crates/services/bcs-system-message/src/producers/session_context_test.rs"

  grep -F 'else if has_provider_downlink_bot' "$source" >/dev/null || \
    fail "Provider groups must detect downlink participants during SessionContext delivery"
  grep -F 'DeliveryType::Inject' "$source" >/dev/null || \
    fail "Provider groups must inject SessionContext instead of starting a hidden driver turn"
  grep -F 'provider_downlink_group_initializes_driver_with_inject' "$test_source" >/dev/null || \
    fail "Provider SessionContext delivery must retain its Rust regression test"
}

test_local_frontend_hides_stale_claude_roles() {
  local network groups
  network="${ROOT}/src/frontend/src/pages/GroupChat/hooks/useBotNetwork.ts"
  groups="${ROOT}/src/frontend/src/pages/GroupChat/hooks/useGroups.ts"

  grep -F 'filterStaleLocalClaudeRoles' "$network" >/dev/null || \
    fail "Bot tabs must filter stale local Claude roles"
  grep -F 'filterStaleLocalClaudeRoles' "$groups" >/dev/null || \
    fail "Bot picker must filter stale local Claude roles"
  grep -F 'Claude (Planner|Developer|Reviewer)' "$network" >/dev/null || \
    fail "Bot tab filtering must be scoped to the three local Claude roles"
}

test_all_start_rolls_back_started_services_on_failure
test_mixed_all_preflight_rejects_external_port_before_restart
test_backend_health_failure_stops_backend
test_backend_wait_fails_when_started_process_exits
test_frontend_start_prepares_dependencies_before_launch
test_frontend_deps_require_dev_commands
test_frontend_install_includes_dev_dependencies
test_frontend_stdin_keeper_survives_singlebox_exit
test_frontend_human_sender_uses_workbench_bot_id
test_bcsfuse_status_loads_runtime_before_pid_lookup
test_service_modules_use_ownership_aware_stop_helpers
test_service_starts_fail_when_ports_remain_occupied
test_baas_stop_does_not_delegate_to_app_stop
test_baas_ready_requires_its_health_endpoint
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
test_frontend_only_banner_does_not_claim_full_stack_ready
test_backend_separates_profile_env_and_workspace_folder
test_restart_all_preflights_before_stopping
test_bcs_baas_provider_uses_h2c_and_owned_lifecycle
test_bcs_sse_diagnostics_do_not_log_raw_chat_payloads
test_bcs_bot_diagnostics_do_not_log_raw_frames_or_tokens
test_claude_relay_diagnostics_do_not_log_token_previews
test_provider_session_context_is_silent_by_default
test_local_frontend_hides_stale_claude_roles

printf 'PASS: singlebox service guard tests\n'
