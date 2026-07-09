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

setup_env() {
  unset SINGLEBOX_MODEL_CONFIG_MODE
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

test_mock_generates_no_real_provider() {
  setup_env
  export SINGLEBOX_MODEL_CONFIG_MODE="mock"

  # shellcheck source=/dev/null
  source "$MODULE"
  singlebox_model_config_prepare

  jq -e '
    .models.mode == "merge"
    and (.models.providers | length) == 0
    and .agents.defaults.models == {}
  ' "$SINGLEBOX_MODEL_CONFIG_FILE" >/dev/null || fail "mock runtime config mismatch"
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

test_manual_generates_runtime_config_from_env
test_manual_requires_complete_env
test_home_copies_only_model_fields
test_home_ignores_non_object_agents_when_models_exist
test_home_requires_confirmation
test_mock_generates_no_real_provider
test_prompt_maps_selection_to_mode
test_noninteractive_defaults_to_mock_without_stdout_noise

printf 'PASS: singlebox model config module tests\n'
