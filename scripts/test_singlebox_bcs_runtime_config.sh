#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT

export PROJECT_ROOT="$ROOT"
export SCRIPT_DIR="${ROOT}/scripts"
export DEP_DIR="${TEST_ROOT}/dependencies"
export LOG_DIR="${DEP_DIR}/logs"
export BCS_DIR="${TEST_ROOT}/bcs"
export BCS_PORT="21000"
export BCS_RUNTIME_CONFIG_DIR="${DEP_DIR}/bcs-config"
mkdir -p "${LOG_DIR}" "${BCS_DIR}/configs"

log_info() { printf '[INFO] %s\n' "$*"; }
log_warn() { printf '[WARN] %s\n' "$*"; }
log_error() { printf '[ERROR] %s\n' "$*" >&2; }

# shellcheck source=/dev/null
source "${ROOT}/scripts/modules/bcs.sh"

# Runtime resource copying is outside this test's configuration boundary.
prepare_bcs_runtime_config_resources() { return 0; }

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

write_local_template() {
    cat > "${BCS_DIR}/configs/bcs-config-local.toml" <<'EOF'
bind = "127.0.0.1"
port = 21000
bcs_endpoint = "http://127.0.0.1:21000"

[llm]
type = "openai_compatible"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
api_key = "legacy-template-secret"
model = "gpt-4.1-mini"
timeout_ms = 120000
temperature = 0
max_tokens = 4096
structured_output = "json_schema"

[security.outbound_url]
block_private_networks = true
allow_loopback = false
EOF
}

reset_case() {
    local name="$1"
    write_local_template
    export BCS_CONFIG_DIR="${TEST_ROOT}/${name}"
    export BCS_SERVER_ENV="local"
    unset BCS_E2E_MOCK_BASE_URL BCS_E2E_JUDGE_API_KEY
    unset OPENCLAW_OPENAI_BASE_URL OPENCLAW_OPENAI_API_KEY OPENCLAW_OPENAI_MODEL_ID
}

assert_contains() {
    local file="$1"
    local expected="$2"
    grep -F "$expected" "$file" >/dev/null || fail "${file} does not contain: ${expected}"
}

assert_not_contains() {
    local file="$1"
    local unexpected="$2"
    if grep -F "$unexpected" "$file" >/dev/null; then
        fail "${file} unexpectedly contains: ${unexpected}"
    fi
}

test_local_llm_stays_disabled_without_env_config() {
    reset_case "default"

    prepare_bcs_runtime_config

    assert_contains "${BCS_CONFIG_DIR}/bcs-config.toml" 'type = "none"'
    assert_contains "${BCS_CONFIG_DIR}/bcs-config-local.toml" 'type = "none"'
    assert_not_contains "${BCS_CONFIG_DIR}/bcs-config.toml" 'legacy-template-secret'
    assert_not_contains "${BCS_CONFIG_DIR}/bcs-config-local.toml" 'legacy-template-secret'
}

test_complete_env_config_enables_local_llm() {
    reset_case "configured"
    export OPENCLAW_OPENAI_BASE_URL='https://llm.example.test/v1'
    export OPENCLAW_OPENAI_API_KEY='secret-that-must-not-be-serialized'
    export OPENCLAW_OPENAI_MODEL_ID='judge-model'

    prepare_bcs_runtime_config

    local config_file
    for config_file in \
        "${BCS_CONFIG_DIR}/bcs-config.toml" \
        "${BCS_CONFIG_DIR}/bcs-config-local.toml"; do
        assert_contains "$config_file" 'type = "openai_compatible"'
        assert_contains "$config_file" 'base_url = "https://llm.example.test/v1"'
        assert_contains "$config_file" 'api_key_env = "OPENCLAW_OPENAI_API_KEY"'
        assert_contains "$config_file" 'model = "judge-model"'
        assert_not_contains "$config_file" "$OPENCLAW_OPENAI_API_KEY"
        assert_not_contains "$config_file" 'legacy-template-secret'
    done
}

test_partial_env_config_does_not_enable_local_llm() {
    reset_case "partial"
    export OPENCLAW_OPENAI_BASE_URL='https://llm.example.test/v1'

    prepare_bcs_runtime_config

    assert_contains "${BCS_CONFIG_DIR}/bcs-config.toml" 'type = "none"'
}

test_e2e_mock_takes_precedence_over_env_config() {
    reset_case "e2e"
    export OPENCLAW_OPENAI_BASE_URL='https://llm.example.test/v1'
    export OPENCLAW_OPENAI_API_KEY='real-provider-secret'
    export OPENCLAW_OPENAI_MODEL_ID='real-provider-model'
    export BCS_E2E_MOCK_BASE_URL='http://127.0.0.1:39090'
    export BCS_E2E_JUDGE_API_KEY='e2e-secret'

    prepare_bcs_runtime_config

    assert_contains "${BCS_CONFIG_DIR}/bcs-config.toml" 'base_url = "http://127.0.0.1:39090/v1"'
    assert_contains "${BCS_CONFIG_DIR}/bcs-config.toml" 'api_key_env = "BCS_E2E_JUDGE_API_KEY"'
    assert_contains "${BCS_CONFIG_DIR}/bcs-config.toml" 'model = "e2e-judge"'
    assert_not_contains "${BCS_CONFIG_DIR}/bcs-config.toml" 'real-provider-model'
}

test_local_llm_stays_disabled_without_env_config
test_complete_env_config_enables_local_llm
test_partial_env_config_does_not_enable_local_llm
test_e2e_mock_takes_precedence_over_env_config

printf 'PASS: singlebox BCS runtime config tests\n'
