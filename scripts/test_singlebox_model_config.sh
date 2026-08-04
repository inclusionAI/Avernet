#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODULE="${ROOT}/scripts/modules/model_config.sh"

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

file_mode() {
  stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1"
}

setup_env() {
  unset SINGLEBOX_MODEL_CONFIG_MODE
  unset SINGLEBOX_MOCK_MODEL_PORT
  unset OPENCLAW_OPENAI_PROVIDER_ID
  unset OPENCLAW_OPENAI_BASE_URL
  unset OPENCLAW_OPENAI_API_KEY
  unset OPENCLAW_OPENAI_MODEL_ID
  unset OPENCLAW_OPENAI_MODEL_NAME
  unset OPENCLAW_OPENAI_MODEL_API
  unset OPENCLAW_MODEL_CONFIG_SOURCE
  unset SINGLEBOX_MODEL_CONFIG_FILE
  unset SINGLEBOX_MODEL_CONFIG_PREPARED
  unset SINGLEBOX_MODEL_CONFIG_HOME_CONFIRMED

  export PROJECT_ROOT="$ROOT"
  export SCRIPT_DIR="${ROOT}/scripts"
  export DEP_DIR="$(mktemp -d)"
  export LOG_DIR="${DEP_DIR}/logs"
  export OPENCLAW_CONFIG_FILE="${DEP_DIR}/home-openclaw/openclaw.json"
  mkdir -p "$LOG_DIR"

  log_info() { printf '[INFO] %s\n' "$*"; }
  log_warn() { printf '[WARN] %s\n' "$*"; }
  log_error() { printf '[ERROR] %s\n' "$*" >&2; }
}

test_manual_generates_runtime_config_from_env() {
  setup_env
  export SINGLEBOX_MODEL_CONFIG_MODE="manual"
  export OPENCLAW_OPENAI_PROVIDER_ID="test-provider"
  export OPENCLAW_OPENAI_BASE_URL="https://model.example.test/v1"
  export OPENCLAW_OPENAI_API_KEY="sk-test"
  export OPENCLAW_OPENAI_MODEL_ID="model-a"
  export OPENCLAW_OPENAI_MODEL_NAME="Model A"

  # shellcheck source=/dev/null
  source "$MODULE"
  singlebox_model_config_prepare

  [ -f "$SINGLEBOX_MODEL_CONFIG_FILE" ] || fail "runtime model config missing"
  assert_eq "600" "$(file_mode "$SINGLEBOX_MODEL_CONFIG_FILE")" "manual config file mode"
  jq -e '
    .models.providers["test-provider"].baseUrl == "https://model.example.test/v1"
    and .models.providers["test-provider"].apiKey == "sk-test"
    and .models.providers["test-provider"].models[0].id == "model-a"
    and .agents.defaults.model.primary == "test-provider/model-a"
  ' "$SINGLEBOX_MODEL_CONFIG_FILE" >/dev/null || fail "manual runtime config mismatch"
  assert_eq "$SINGLEBOX_MODEL_CONFIG_FILE" "$OPENCLAW_MODEL_CONFIG_SOURCE" "5bot source"
}

test_manual_requires_complete_env() {
  setup_env
  export SINGLEBOX_MODEL_CONFIG_MODE="manual"
  export OPENCLAW_OPENAI_BASE_URL="https://model.example.test/v1"
  export OPENCLAW_OPENAI_API_KEY="sk-test"

  # shellcheck source=/dev/null
  source "$MODULE"
  if singlebox_model_config_prepare; then
    fail "manual mode should fail without OPENCLAW_OPENAI_MODEL_ID"
  fi
}

test_home_copies_only_model_fields() {
  setup_env
  export SINGLEBOX_MODEL_CONFIG_MODE="home"
  export SINGLEBOX_MODEL_CONFIG_HOME_CONFIRMED=1
  mkdir -p "$(dirname "$OPENCLAW_CONFIG_FILE")"
  cat > "$OPENCLAW_CONFIG_FILE" <<'JSON'
{
  "models": {
    "mode": "merge",
    "providers": {
      "home-provider": {
        "baseUrl": "https://home.example.test/v1",
        "apiKey": "home-key",
        "api": "openai-completions",
        "models": [{"id": "home-model", "name": "Home Model", "input": ["text"]}]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {"primary": "home-provider/home-model"},
      "models": {"home-provider/home-model": {"alias": "Home Model"}},
      "workspace": "/should/not/copy"
    }
  },
  "gateway": {"port": 9999}
}
JSON

  # shellcheck source=/dev/null
  source "$MODULE"
  singlebox_model_config_prepare

  assert_eq "600" "$(file_mode "$SINGLEBOX_MODEL_CONFIG_FILE")" "home config file mode"
  jq -e '
    .models.providers["home-provider"].apiKey == "home-key"
    and .agents.defaults.model.primary == "home-provider/home-model"
    and (.agents.defaults | has("workspace") | not)
    and (. | has("gateway") | not)
  ' "$SINGLEBOX_MODEL_CONFIG_FILE" >/dev/null || fail "home runtime config mismatch"
}

test_home_ignores_non_object_agents_when_models_exist() {
  setup_env
  export SINGLEBOX_MODEL_CONFIG_MODE="home"
  export SINGLEBOX_MODEL_CONFIG_HOME_CONFIRMED=1
  mkdir -p "$(dirname "$OPENCLAW_CONFIG_FILE")"
  cat > "$OPENCLAW_CONFIG_FILE" <<'JSON'
{
  "models": {
    "mode": "merge",
    "providers": {
      "home-provider": {
        "baseUrl": "https://home.example.test/v1",
        "apiKey": "home-key",
        "models": [{"id": "home-model", "name": "Home Model"}]
      }
    }
  },
  "agents": []
}
JSON

  # shellcheck source=/dev/null
  source "$MODULE"
  singlebox_model_config_prepare

  jq -e '
    .models.providers["home-provider"].apiKey == "home-key"
    and .agents.defaults == {}
  ' "$SINGLEBOX_MODEL_CONFIG_FILE" >/dev/null || fail "home mode should ignore non-object agents"
}

test_home_requires_confirmation() {
  setup_env
  export SINGLEBOX_MODEL_CONFIG_MODE="home"
  mkdir -p "$(dirname "$OPENCLAW_CONFIG_FILE")"
  cat > "$OPENCLAW_CONFIG_FILE" <<'JSON'
{
  "models": {
    "mode": "merge",
    "providers": {}
  }
}
JSON

  # shellcheck source=/dev/null
  source "$MODULE"
  if singlebox_model_config_prepare; then
    fail "home mode should require explicit confirmation"
  fi
}

test_mock_generates_local_provider() {
  setup_env
  export SINGLEBOX_MODEL_CONFIG_MODE="mock"

  # shellcheck source=/dev/null
  source "$MODULE"
  singlebox_model_config_prepare

  jq -e '
    .models.mode == "merge"
    and .models.providers["singlebox-mock"].baseUrl == "http://127.0.0.1:18080/v1"
    and .models.providers["singlebox-mock"].api == "openai-completions"
    and .models.providers["singlebox-mock"].models[0].id == "singlebox-mock"
    and .agents.defaults.model.primary == "singlebox-mock/singlebox-mock"
  ' "$SINGLEBOX_MODEL_CONFIG_FILE" >/dev/null || fail "mock runtime config mismatch"
}

test_mock_port_override_updates_local_provider() {
  setup_env
  export SINGLEBOX_MODEL_CONFIG_MODE="mock"
  export SINGLEBOX_MOCK_MODEL_PORT="28080"

  # shellcheck source=/dev/null
  source "$MODULE"
  singlebox_model_config_prepare

  assert_eq \
    "http://127.0.0.1:28080/v1" \
    "$(jq -r '.models.providers["singlebox-mock"].baseUrl' "$SINGLEBOX_MODEL_CONFIG_FILE")" \
    "mock provider port override"
}

test_prompt_maps_selection_to_mode() {
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"

  selected="$(printf '2\n' | singlebox_model_config_prompt)"
  assert_eq "manual" "$selected" "manual selection"
}

test_noninteractive_defaults_to_mock_without_stdout_noise() {
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"

  selected="$(singlebox_model_config_select_mode 2>/dev/null)"
  assert_eq "mock" "$selected" "noninteractive default"
}

test_model_config_is_required_only_for_consumers() {
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"

  local service
  for service in all baas bots bcs_bots; do
    singlebox_model_config_required_for_services "$service" \
      || fail "${service} should require model config"
  done

  for service in bcs bcs_frontend frontend backend bcsfuse; do
    if singlebox_model_config_required_for_services "$service"; then
      fail "${service} should not require model config"
    fi
  done

  singlebox_model_config_required_for_services bcs frontend bots \
    || fail "a mixed target containing bots should require model config"
}

test_mock_start_is_only_managed_in_mock_mode() {
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"

  (
    MOCK_READY_CALLS=0
    local pid_file
    pid_file="$(singlebox_mock_model_pid_file)"
    singlebox_mock_model_is_ready() {
      MOCK_READY_CALLS=$((MOCK_READY_CALLS + 1))
      return 0
    }

    local mode
    for mode in "" manual home; do
      SINGLEBOX_MODEL_CONFIG_MODE="$mode"
      singlebox_mock_model_start
      assert_eq "0" "$MOCK_READY_CALLS" "${mode:-unset} mode start"
    done

    SINGLEBOX_MODEL_CONFIG_MODE="mock"
    singlebox_mock_model_start
    assert_eq "1" "$MOCK_READY_CALLS" "mock mode start"
    assert_eq "0" "$SINGLEBOX_MOCK_MODEL_STARTED_BY_COMMAND" "external ready service ownership"
    [ ! -f "$pid_file" ] || fail "reused external mock service should not create a PID file"
  )
}

test_mock_stop_preserves_pid_on_identity_mismatch() {
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"

  local pid_file pid
  pid_file="$(singlebox_mock_model_pid_file)"
  mkdir -p "$(dirname "$pid_file")"
  printf '%s\n' "$$" > "$pid_file"

  unset SINGLEBOX_MODEL_CONFIG_MODE
  singlebox_mock_model_stop

  kill -0 "$$" 2>/dev/null || fail "mock stop terminated a non-mock process"
  [ -f "$pid_file" ] || fail "identity mismatch should preserve the PID file"
}

test_mock_stop_cross_process_without_current_mode() {
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"

  local port pid_file pid attempt
  port="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  export SINGLEBOX_MOCK_MODEL_PORT="$port"
  pid_file="$(singlebox_mock_model_pid_file)"

  SINGLEBOX_MODEL_CONFIG_MODE=mock bash -c '
    log_info() { :; }
    log_warn() { :; }
    log_error() { printf "%s\n" "$*" >&2; }
    source "$1"
    singlebox_mock_model_start
  ' _ "$MODULE"

  [ -f "$pid_file" ] || fail "mock start should create the owned PID file"
  pid="$(tr -d '\r\n' < "$pid_file")"
  kill -0 "$pid" 2>/dev/null || fail "mock process should be running after process A exits"

  unset SINGLEBOX_MODEL_CONFIG_MODE
  bash -c '
    log_info() { :; }
    log_warn() { :; }
    log_error() { printf "%s\n" "$*" >&2; }
    source "$1"
    singlebox_mock_model_stop
  ' _ "$MODULE"

  for attempt in $(seq 1 100); do
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 0.05
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    rm -f "$pid_file"
    fail "process B should stop the mock process without a mode variable"
  fi
  [ ! -f "$pid_file" ] || fail "successful cross-process stop should remove the PID file"
}

test_mock_external_service_is_not_owned_or_stopped() (
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"

  local port pid_file external_pid="" attempt
  port="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  export SINGLEBOX_MOCK_MODEL_PORT="$port"
  pid_file="$(singlebox_mock_model_pid_file)"

  trap '
    if [ -n "$external_pid" ] && kill -0 "$external_pid" 2>/dev/null; then
      kill "$external_pid" 2>/dev/null || true
      wait "$external_pid" 2>/dev/null || true
    fi
  ' EXIT

  python3 "${PROJECT_ROOT}/scripts/modules/mock_model_server.py" \
    --port "$port" >"${LOG_DIR}/external-mock.log" 2>&1 &
  external_pid=$!
  for attempt in $(seq 1 100); do
    if singlebox_mock_model_is_ready; then
      break
    fi
    sleep 0.05
  done
  singlebox_mock_model_is_ready || fail "external mock service should become ready"

  SINGLEBOX_MODEL_CONFIG_MODE=mock
  singlebox_mock_model_start
  assert_eq "0" "$SINGLEBOX_MOCK_MODEL_STARTED_BY_COMMAND" "external mock ownership"
  [ ! -f "$pid_file" ] || fail "reused external mock service should not create an owned PID"

  unset SINGLEBOX_MODEL_CONFIG_MODE
  singlebox_mock_model_stop
  kill -0 "$external_pid" 2>/dev/null || fail "stop should not terminate an external mock service"
  [ ! -f "$pid_file" ] || fail "external mock service should remain unowned after stop"
)

test_mock_stop_removes_stale_pid_without_current_mode() {
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"

  local pid_file
  pid_file="$(singlebox_mock_model_pid_file)"
  mkdir -p "$(dirname "$pid_file")"
  printf '99999999\n' > "$pid_file"

  unset SINGLEBOX_MODEL_CONFIG_MODE
  singlebox_mock_model_stop

  [ ! -f "$pid_file" ] || fail "stale mock PID file should be removed"
}

test_mock_stop_keeps_pid_when_process_cannot_be_stopped() {
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"

  (
    local pid_file
    pid_file="$(singlebox_mock_model_pid_file)"
    mkdir -p "$(dirname "$pid_file")"
    printf '4242\n' > "$pid_file"

    kill() {
      if [ "${1:-}" = "-0" ]; then
        return 0
      fi
      return 1
    }
    ps() {
      printf 'python3 %s/scripts/modules/mock_model_server.py --port 18080\n' "$PROJECT_ROOT"
    }

    unset SINGLEBOX_MODEL_CONFIG_MODE
    if singlebox_mock_model_stop; then
      fail "mock stop should fail when the owned process cannot be stopped"
    fi
    [ -f "$pid_file" ] || fail "failed stop should preserve the PID file"
  )
}

test_mock_stop_service_scope_is_all_only() {
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"

  singlebox_mock_model_stop_required_for_services all \
    || fail "stop all should stop the shared mock server"

  local service
  for service in baas bots bcs_bots bcs frontend backend; do
    if singlebox_mock_model_stop_required_for_services "$service"; then
      fail "stop ${service} should not stop the shared mock server"
    fi
  done

  singlebox_mock_model_stop_required_for_services bots all \
    || fail "a mixed target containing all should stop the shared mock server"
}

test_singlebox_stop_command_uses_shared_mock_scope() (
  setup_env
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/singlebox.sh"

  export DEP_DIR="$(mktemp -d)"
  export LOG_DIR="${DEP_DIR}/logs"
  export STANDALONE_RUNTIME_DIR="${DEP_DIR}/standalone"
  LOCAL_MODE=false
  STANDALONE_MODE=false
  stopped_services=""
  mock_stop_calls=0

  load_engine_type() { :; }
  apply_singlebox_mode_defaults() { :; }
  resolve_bcs_server_env() { :; }
  ensure_git_hooks_installed() { :; }
  bcn_plugin_mode() { printf '%s\n' source; }
  stop_service() {
    stopped_services="${stopped_services}${stopped_services:+ }$1"
  }
  singlebox_mock_model_stop() {
    mock_stop_calls=$((mock_stop_calls + 1))
  }

  main stop bots
  assert_eq "bots" "$stopped_services" "stop bots target"
  assert_eq "0" "$mock_stop_calls" "stop bots shared mock calls"

  main stop all
  assert_eq "bots all" "$stopped_services" "stop all target"
  assert_eq "1" "$mock_stop_calls" "stop all shared mock calls"
)

test_mock_health_requires_exact_response() (
  setup_env
  # shellcheck source=/dev/null
  source "$MODULE"

  curl() {
    printf '%s\n' '{"status":"ok","service":"singlebox-mock-model"}'
  }
  singlebox_mock_model_is_ready || fail "exact mock health response should be ready"

  curl() {
    printf '%s\n' '{"status":"ok","service":"singlebox-mock-model","extra":true}'
  }
  if singlebox_mock_model_is_ready; then
    fail "health response with extra fields should not be ready"
  fi

  curl() {
    printf '%s\n' '{"status":"starting","service":"singlebox-mock-model"}'
  }
  if singlebox_mock_model_is_ready; then
    fail "non-ok health response should not be ready"
  fi
)

test_manual_generates_runtime_config_from_env
test_manual_requires_complete_env
test_home_copies_only_model_fields
test_home_ignores_non_object_agents_when_models_exist
test_home_requires_confirmation
test_mock_generates_local_provider
test_mock_port_override_updates_local_provider
test_prompt_maps_selection_to_mode
test_noninteractive_defaults_to_mock_without_stdout_noise
test_model_config_is_required_only_for_consumers
test_mock_health_requires_exact_response
test_mock_start_is_only_managed_in_mock_mode
test_mock_stop_preserves_pid_on_identity_mismatch
test_mock_stop_cross_process_without_current_mode
test_mock_external_service_is_not_owned_or_stopped
test_mock_stop_removes_stale_pid_without_current_mode
test_mock_stop_keeps_pid_when_process_cannot_be_stopped
test_mock_stop_service_scope_is_all_only
test_singlebox_stop_command_uses_shared_mock_scope

printf 'PASS: singlebox model config module tests\n'
