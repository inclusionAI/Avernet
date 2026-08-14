#!/usr/bin/env bash
# Regression coverage for OpenClaw-only, mixed-provider, and legacy alias modes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"

cleanup() {
    rm -rf "$TMP"
}
trap cleanup EXIT

CLAUDE_PROFILE="$TMP/claude-profile"
cp -R "$ROOT/scripts/4bots_merchant_operations_profile_for_claude" "$CLAUDE_PROFILE"
mkdir -p "$TMP/claude-config" "$TMP/claude-workspace"
python3 - "$CLAUDE_PROFILE/bots.json" "$TMP" <<'PY'
import json
import sys

path, temp_root = sys.argv[1:]
with open(path, encoding='utf-8') as stream:
    profile = json.load(stream)
runtime = profile['bots'][0]['runtime']
runtime['claude_config_dir'] = f'{temp_root}/claude-config'
runtime['workspace'] = f'{temp_root}/claude-workspace'
with open(path, 'w', encoding='utf-8') as stream:
    json.dump(profile, stream)
PY

export BOTS_PROFILE_DIR="$ROOT/scripts/4bots_merchant_operations_profile"
export BOTS_EXCLUDED_PROFILE_SOURCE="platform-data"
export CLAUDE_PROFILE_DIR="$CLAUDE_PROFILE"
export SINGLEBOX_MODEL_CONFIG_MODE="home"
export HYBRID_STATE_FILE="$TMP/hybrid-state.json"
# shellcheck source=/dev/null
source "$ROOT/scripts/singlebox.sh"

hybrid_validate_profiles
[[ "$(bots_dynamic_count)" == "3" ]]
[[ "$(bots_dynamic_specs | cut -f4 | tr '\n' ' ')" == *"merchant-operations"* ]]
[[ "$(bots_dynamic_specs | cut -f4 | tr '\n' ' ')" != *"platform-data"* ]]
[[ "$(bots_dynamic_specs | awk -F '\t' '$4 == "platform-supply-chain" { print $3 }')" == "30631" ]]

MANAGER_WORKSPACE="$TMP/manager-workspace"
mkdir -p "$MANAGER_WORKSPACE"
touch "$MANAGER_WORKSPACE/TOOLS.md"
bots_dynamic_copy_profile_files merchant-operations "$MANAGER_WORKSPACE"
bots_dynamic_setup_bcs_skill "$MANAGER_WORKSPACE"
for profile_file in AGENTS.md IDENTITY.md KNOWLEDGE.md; do
    [[ -f "$MANAGER_WORKSPACE/$profile_file" ]]
done
[[ ! -e "$MANAGER_WORKSPACE/TOOLS.md" ]]
for reference in \
    'skills/bcs-coordination/SKILL.md' \
    'skills/bcs-coordination/references/custom-collaboration.md' \
    'skills/bcs-coordination/references/custom-collaboration-schema.md'; do
    [[ -f "$MANAGER_WORKSPACE/$reference" ]]
done
grep -Fq 'skills/bcs-coordination/references/custom-collaboration.md' "$MANAGER_WORKSPACE/AGENTS.md"
grep -Fq 'skills/bcs-coordination/references/custom-collaboration-schema.md' "$MANAGER_WORKSPACE/AGENTS.md"

grep -Fq 'AskUserQuestion' "$CLAUDE_PROFILE/platform-data/CLAUDE.md"
jq -e '.bots[0].runtime | has("model") | not' "$CLAUDE_PROFILE/bots.json" >/dev/null

MODEL_CONFIG="$TMP/model-config.json"
export OPENCLAW_OPENAI_PROVIDER_ID="openai-compatible"
export OPENCLAW_OPENAI_BASE_URL="https://model.example.test/v1"
export OPENCLAW_OPENAI_API_KEY="test-token"
export OPENCLAW_OPENAI_MODEL_ID="glm-local"
export OPENCLAW_OPENAI_MODEL_NAME="GLM Local"
export ANTHROPIC_BASE_URL="https://anthropic-gateway.example.test"
export ANTHROPIC_AUTH_TOKEN="anthropic-test-token"
export ANTHROPIC_MODEL="glm-claude"
singlebox_model_config_write_manual "$MODEL_CONFIG"
export SINGLEBOX_MODEL_CONFIG_FILE="$MODEL_CONFIG"
model_config_before="$(shasum -a 256 "$MODEL_CONFIG" | awk '{print $1}')"
hybrid_apply_model_policy
model_config_after="$(shasum -a 256 "$MODEL_CONFIG" | awk '{print $1}')"
[[ "$model_config_after" == "$model_config_before" ]]
jq -e '
  .agents.defaults.model.primary == "openai-compatible/glm-local"
  and .agents.defaults.models["openai-compatible/glm-local"].alias == "GLM Local"
  and ([.models.providers["openai-compatible"].models[].id] == ["glm-local"])
' "$MODEL_CONFIG" >/dev/null
[[ "$HYBRID_MODEL_ID" == "glm-local" ]]
[[ "$SINGLEBOX_REQUIRED_OPENCLAW_MODEL" == "openai-compatible/glm-local" ]]
[[ "$LLM_FAST_MODEL" == "glm-local" ]]
[[ "$LLM_BALANCED_MODEL" == "glm-local" ]]
[[ "$LLM_REASONING_MODEL" == "glm-local" ]]
[[ "$LLM_LONG_CONTEXT_MODEL" == "glm-local" ]]
[[ "$LLM_EXTRACTION_MODEL" == "glm-local" ]]

export HYBRID_CLAUDE_CONFIG_MODE="user"
hybrid_apply_model_policy
[[ -z "${HYBRID_MODEL_ID:-}" ]]
[[ "$SINGLEBOX_REQUIRED_OPENCLAW_MODEL" == "openai-compatible/glm-local" ]]
[[ "$LLM_FAST_MODEL" == "glm-local" ]]
unset HYBRID_CLAUDE_CONFIG_MODE
hybrid_apply_model_policy

IFS=$'\x1f' read -r role _ _ port config_dir workspace model prompt permission < <(claude_profile_entries)
[[ "$role" == "platform-data" ]]
[[ "$port" == "18900" ]]
[[ "$model" == "glm-claude" ]]
[[ "$permission" == "bypassPermissions" ]]
[[ "$config_dir" == "$TMP/claude-config" ]]
[[ "$workspace" == "$TMP/claude-workspace" ]]
[[ "$prompt" == "$CLAUDE_PROFILE/platform-data/CLAUDE.md" ]]

export SINGLEBOX_MODEL_CONFIG_MODE="manual"
unset HYBRID_CLAUDE_CONFIG_MODE
claude_relays_manual_model_env "$model"
[[ "${CLAUDE_RELAY_MANUAL_MODEL_ENV[*]}" == *"ANTHROPIC_BASE_URL=https://anthropic-gateway.example.test"* ]]
[[ "${CLAUDE_RELAY_MANUAL_MODEL_ENV[*]}" == *"ANTHROPIC_AUTH_TOKEN=anthropic-test-token"* ]]
[[ "${CLAUDE_RELAY_MANUAL_MODEL_ENV[*]}" == *"ANTHROPIC_MODEL=glm-claude"* ]]
[[ "${CLAUDE_RELAY_MANUAL_MODEL_ENV[*]}" == *"ANTHROPIC_SMALL_FAST_MODEL=glm-claude"* ]]
export HYBRID_CLAUDE_CONFIG_MODE="user"
claude_relays_manual_model_env "$model"
[[ "${#CLAUDE_RELAY_MANUAL_MODEL_ENV[@]}" == "0" ]]
IFS=$'\x1f' read -r _ _ _ _ _ _ user_model _ _ < <(claude_profile_entries)
[[ -z "$user_model" ]]
unset HYBRID_CLAUDE_CONFIG_MODE
export SINGLEBOX_MODEL_CONFIG_MODE="home"
claude_relays_manual_model_env "$model"
[[ "${#CLAUDE_RELAY_MANUAL_MODEL_ENV[@]}" == "0" ]]

test_claude_relay_build_uses_configured_registry() (
    local gateway="$TMP/claude-relay-gateway"
    local npm_calls="$TMP/claude-relay-npm-calls"
    mkdir -p "$gateway/src"
    printf '%s\n' '{}' > "$gateway/package.json"
    printf '%s\n' 'source' > "$gateway/src/server.ts"

    CLAUDE_RELAY_GATEWAY_DIR="$gateway"
    CLAUDE_RELAY_LOG="$TMP/claude-relay.log"
    NPM_REGISTRY_URL="https://registry.example.test"
    claude_relays_enabled() { return 0; }
    claude_profile_validate_config() { return 0; }
    npm() { printf '%s\n' "$*" >> "$npm_calls"; }

    claude_relays_setup
    grep -Fxq 'install --include=dev --ignore-scripts --no-audit --no-fund --registry=https://registry.example.test' "$npm_calls"
    grep -Fxq 'run prepublishOnly' "$npm_calls"
)

test_claude_relay_build_uses_configured_registry

export BCSFUSE_RUNTIME_DIR="$TMP/bcsfuse-runtime"
mkdir -p "$BCSFUSE_RUNTIME_DIR/env"
cat > "$BCSFUSE_RUNTIME_DIR/env/.env.local" <<'ENV'
export LLM_FAST_MODEL="runtime-override"
export LLM_BALANCED_MODEL="runtime-override"
export LLM_REASONING_MODEL="runtime-override"
export LLM_LONG_CONTEXT_MODEL="runtime-override"
export LLM_EXTRACTION_MODEL="runtime-override"
ENV
unset HYBRID_CLAUDE_ACTIVE MERCHANT_HYBRID_ACTIVE
bcsfuse_load_env
[[ "$LLM_FAST_MODEL" == "runtime-override" ]]
[[ "$LLM_BALANCED_MODEL" == "runtime-override" ]]
[[ "$LLM_REASONING_MODEL" == "runtime-override" ]]
[[ "$LLM_LONG_CONTEXT_MODEL" == "runtime-override" ]]
[[ "$LLM_EXTRACTION_MODEL" == "runtime-override" ]]

export HYBRID_CLAUDE_ACTIVE=1
bcsfuse_load_env
[[ "$LLM_FAST_MODEL" == "glm-local" ]]
[[ "$LLM_BALANCED_MODEL" == "glm-local" ]]
[[ "$LLM_REASONING_MODEL" == "glm-local" ]]
[[ "$LLM_LONG_CONTEXT_MODEL" == "glm-local" ]]
[[ "$LLM_EXTRACTION_MODEL" == "glm-local" ]]

unset BOTS_EXCLUDED_PROFILE_SOURCE
[[ "$(bots_dynamic_count)" == "4" ]]
export BOTS_EXCLUDED_PROFILE_SOURCE="platform-data"

unset BOTS_EXCLUDED_PROFILE_SOURCE CLAUDE_PROFILE_DIR HYBRID_CLAUDE_ACTIVE MERCHANT_HYBRID_ACTIVE
hybrid_validate_profiles
hybrid_configure_mode
[[ "${HYBRID_START_ORDER[*]}" == "bcs bcsfuse bots frontend" ]]
[[ "$(bots_dynamic_count)" == "4" ]]
export BOTS_EXCLUDED_PROFILE_SOURCE="platform-data"
export CLAUDE_PROFILE_DIR="$CLAUDE_PROFILE"
hybrid_configure_mode
[[ "${HYBRID_START_ORDER[*]}" == "${HYBRID_CLAUDE_START_ORDER[*]}" ]]

unset BOTS_EXCLUDED_PROFILE_SOURCE
if hybrid_validate_profiles; then
    echo 'Claude profile without an excluded OpenClaw source unexpectedly accepted' >&2
    exit 1
fi
unset CLAUDE_PROFILE_DIR
export BOTS_EXCLUDED_PROFILE_SOURCE="platform-data"
if hybrid_validate_profiles; then
    echo 'excluded OpenClaw source without a Claude profile unexpectedly accepted' >&2
    exit 1
fi
export CLAUDE_PROFILE_DIR="$CLAUDE_PROFILE"

unset CLAUDE_PROFILE_DIR
if validate_hybrid_profile_options hybrid; then
    echo 'incomplete hybrid profile options unexpectedly accepted by CLI validation' >&2
    exit 1
fi
export CLAUDE_PROFILE_DIR="$CLAUDE_PROFILE"
validate_hybrid_profile_options hybrid

unset BOTS_PROFILE_DIR
if validate_hybrid_profile_options hybrid; then
    echo 'Claude replacement options without an OpenClaw profile unexpectedly accepted' >&2
    exit 1
fi
export BOTS_PROFILE_DIR="$ROOT/scripts/4bots_merchant_operations_profile"

python3 - "$CLAUDE_PROFILE/bots.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding='utf-8') as stream:
    profile = json.load(stream)
profile['bots'][0]['runtime']['relay_port'] = 18901
with open(path, 'w', encoding='utf-8') as stream:
    json.dump(profile, stream)
PY
if hybrid_validate_profiles; then
    echo 'invalid relay port unexpectedly accepted' >&2
    exit 1
fi
python3 - "$CLAUDE_PROFILE/bots.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding='utf-8') as stream:
    profile = json.load(stream)
profile['bots'][0]['runtime']['relay_port'] = 18900
with open(path, 'w', encoding='utf-8') as stream:
    json.dump(profile, stream)
PY

test_provider_bot_registration_keeps_profile_display_name() (
    export CLAUDE_BOTS_STATE_FILE="$TMP/claude-bots-state.json"
    export BCS_BAAS_PROVIDER_STATE_FILE="$TMP/provider-state.json"
    export BCS_BAAS_PROVIDER_TOKEN_FILE="$TMP/provider-tokens.json"
    export BCS_PORT=21000

    printf '%s\n' '{"entity_id":"mock-user","bots":[{"role":"platform-data","bot_id":"bot-data","name":"平台数据分析"}]}' \
        > "$CLAUDE_BOTS_STATE_FILE"

    local captured_provider_bot_payload
    captured_provider_bot_payload="$TMP/provider-bot-payload.json"

    bcs_baas_provider_bcs_owner_id() { printf '%s\n' '001'; }
    bcs_baas_provider_update_tokens() { :; }
    bcs_baas_provider_add_bot_token() { :; }
    log_info() { :; }
    curl() {
        local data='' url=''
        while [ "$#" -gt 0 ]; do
            case "$1" in
                -d)
                    data="$2"
                    shift 2
                    ;;
                http://*|https://*)
                    url="$1"
                    shift
                    ;;
                *)
                    shift
                    ;;
            esac
        done
        case "$url" in
            */providers)
                printf '%s\n' '{"provider_id":"provider-test","provider_admin_token":"provider-admin-test","bcs_to_provider_token":"bcs-to-provider-test"}'
                ;;
            */providers/provider-test/bots)
                printf '%s' "$data" > "$captured_provider_bot_payload"
                printf '%s\n' '{"bot_runtime_token":"bot-runtime-test","bot_uuid":"bot-provider-test"}'
                ;;
            */bots/bot-provider-test/visibility)
                printf '%s\n' '{"success":true,"data":{"visibility":"public"}}'
                ;;
            *)
                return 1
                ;;
        esac
    }

    bcs_baas_provider_register
    [ "$(jq -r '.name' "$captured_provider_bot_payload")" = '平台数据分析' ]
    ! jq -e '.name | contains("（当前）")' "$captured_provider_bot_payload" >/dev/null
)

test_provider_bot_registration_keeps_profile_display_name

test_provider_registration_is_reused_across_restarts() (
    export CLAUDE_BOTS_STATE_FILE="$TMP/reuse-claude-bots-state.json"
    export BCS_BAAS_PROVIDER_STATE_FILE="$TMP/reuse-provider-state.json"
    export BCS_BAAS_PROVIDER_TOKEN_FILE="$TMP/reuse-provider-tokens.json"
    export BCS_PORT=21000
    local calls="$TMP/reuse-provider-calls"

    printf '%s\n' '{"entity_id":"mock-user","bots":[{"role":"platform-data","bot_id":"bot-data","name":"平台数据分析"}]}' \
        > "$CLAUDE_BOTS_STATE_FILE"
    printf '%s\n' '{"baas_token":"baas","provider_id":"provider-existing","provider_admin_token":"provider-admin","bcs_to_provider_token":"bcs-token","provider_bots":{}}' \
        > "$BCS_BAAS_PROVIDER_TOKEN_FILE"

    bcs_baas_provider_bcs_owner_id() { printf '%s\n' '001'; }
    bcs_baas_provider_add_bot_token() { :; }
    log_info() { :; }
    curl() {
        local method=GET url=''
        while [ "$#" -gt 0 ]; do
            case "$1" in
                -X)
                    method="$2"
                    shift 2
                    ;;
                -d)
                    shift 2
                    ;;
                http://*|https://*)
                    url="$1"
                    shift
                    ;;
                *)
                    shift
                    ;;
            esac
        done
        printf '%s %s\n' "$method" "$url" >> "$calls"
        case "$method $url" in
            "GET http://127.0.0.1:21000/providers/provider-existing")
                printf '%s\n' '{"provider_id":"provider-existing"}'
                ;;
            "POST http://127.0.0.1:21000/providers/provider-existing/bots")
                printf '%s\n' '{"bot_runtime_token":"bot-runtime","bot_uuid":"bot-provider"}'
                ;;
            "PUT http://127.0.0.1:21000/bots/bot-provider/visibility")
                printf '%s\n' '{"success":true,"data":{"visibility":"public"}}'
                ;;
            *)
                return 1
                ;;
        esac
    }

    bcs_baas_provider_register
    grep -Fxq 'GET http://127.0.0.1:21000/providers/provider-existing' "$calls"
    grep -Fxq 'POST http://127.0.0.1:21000/providers/provider-existing/bots' "$calls"
    ! grep -Fxq 'POST http://127.0.0.1:21000/providers' "$calls"
    [ "$(jq -r '.provider_id' "$BCS_BAAS_PROVIDER_STATE_FILE")" = provider-existing ]
)

test_provider_registration_is_reused_across_restarts

test_provider_bot_cleanup_preserves_provider_credentials() (
    export BCS_BAAS_PROVIDER_STATE_FILE="$TMP/cleanup-provider-state.json"
    export BCS_BAAS_PROVIDER_TOKEN_FILE="$TMP/cleanup-provider-tokens.json"
    export BCS_PORT=21000
    local calls="$TMP/cleanup-provider-calls"

    printf '%s\n' '{"provider_id":"provider-existing","bots":[{"provider_bot_ref":"bot-data:mock-user"}]}' \
        > "$BCS_BAAS_PROVIDER_STATE_FILE"
    printf '%s\n' '{"baas_token":"baas","provider_id":"provider-existing","provider_admin_token":"provider-admin","bcs_to_provider_token":"bcs-token","provider_bots":{"platform-data":{"provider_bot_ref":"bot-data:mock-user","bot_runtime_token":"runtime"}}}' \
        > "$BCS_BAAS_PROVIDER_TOKEN_FILE"

    log_info() { :; }
    curl() {
        printf '%s\n' "$*" >> "$calls"
        printf '%s\n' '{}'
    }

    bcs_baas_provider_cleanup_registration
    [ ! -f "$BCS_BAAS_PROVIDER_STATE_FILE" ]
    jq -e '
      .provider_id == "provider-existing"
      and .provider_admin_token == "provider-admin"
      and .bcs_to_provider_token == "bcs-token"
      and (.provider_bots | length) == 0
    ' "$BCS_BAAS_PROVIDER_TOKEN_FILE" >/dev/null
    grep -Fq '/providers/provider-existing/bots/bot-data%3Amock-user' "$calls"
)

test_provider_bot_cleanup_preserves_provider_credentials

test_clean_bots_removes_attached_claude_runtime() (
    export BOTS_PROFILE_DIR="$ROOT/scripts/4bots_merchant_operations_profile"
    export CLAUDE_BOTS_STATE_FILE="$TMP/clean-claude-bots-state.json"
    export HYBRID_STATE_FILE="$TMP/clean-hybrid-state.json"
    local config_dir="$TMP/clean-claude-config"
    local workspace="$TMP/clean-claude-workspace"
    local events="$TMP/clean-claude-events"
    mkdir -p "$config_dir" "$workspace"
    printf '%s\n' '{"setting":true}' > "$config_dir/settings.json"
    printf '%s\n' 'work' > "$workspace/work.txt"
    jq -n \
        --arg profile "$BOTS_PROFILE_DIR" \
        --arg claude_profile "$CLAUDE_PROFILE" \
        --arg config "$config_dir" \
        --arg workspace "$workspace" \
        '{bots_profile_dir: $profile, claude_profile_dir: $claude_profile,
          claude_config_dir: $config, workspace: $workspace, bots: []}' \
        > "$CLAUDE_BOTS_STATE_FILE"
    jq -n \
        --arg profile "$BOTS_PROFILE_DIR" \
        --arg claude_profile "$CLAUDE_PROFILE" \
        '{mode: "claude", bots_profile_dir: $profile, excluded_profile_source: "platform-data",
          claude_profile_dir: $claude_profile}' \
        > "$HYBRID_STATE_FILE"

    bcs_baas_provider_clean() { printf '%s\n' provider >> "$events"; }
    claude_relays_clean() { printf '%s\n' relay >> "$events"; }
    log_info() { :; }

    hybrid_clean_attached_claude_runtime
    [ "$(cat "$events")" = $'provider\nrelay' ]
    [ ! -e "$config_dir" ]
    [ ! -e "$workspace" ]
    [ ! -f "$CLAUDE_BOTS_STATE_FILE" ]
    [ ! -f "$HYBRID_STATE_FILE" ]
)

test_clean_bots_removes_attached_claude_runtime

test_claude_clean_rejects_broad_paths() (
    export CLAUDE_BOTS_STATE_FILE="$TMP/unsafe-clean-claude-bots-state.json"
    jq -n --arg home "$HOME" \
        '{claude_config_dir: $home, workspace: "/", bots: []}' \
        > "$CLAUDE_BOTS_STATE_FILE"
    log_error() { :; }

    if claude_bots_clean; then
        echo 'Claude cleanup unexpectedly accepted a broad runtime path' >&2
        exit 1
    fi
    [ -f "$CLAUDE_BOTS_STATE_FILE" ]
)

test_claude_clean_rejects_broad_paths

test_hybrid_profile_defaults() (
    log_info() { :; }

    export HYBRID_USE_CLAUDE_CODE=yes
    unset BOTS_PROFILE_DIR BOTS_EXCLUDED_PROFILE_SOURCE CLAUDE_PROFILE_DIR
    apply_hybrid_profile_defaults start hybrid
    [ "$BOTS_PROFILE_DIR" = "scripts/4bots_merchant_operations_profile" ]
    [ "$BOTS_EXCLUDED_PROFILE_SOURCE" = "platform-data" ]
    [ "$CLAUDE_PROFILE_DIR" = "scripts/4bots_merchant_operations_profile_for_claude" ]

    export HYBRID_USE_CLAUDE_CODE=no
    unset BOTS_PROFILE_DIR BOTS_EXCLUDED_PROFILE_SOURCE CLAUDE_PROFILE_DIR
    apply_hybrid_profile_defaults start hybrid
    [ "$BOTS_PROFILE_DIR" = "scripts/4bots_merchant_operations_profile" ]
    [ -z "${BOTS_EXCLUDED_PROFILE_SOURCE:-}" ]
    [ -z "${CLAUDE_PROFILE_DIR:-}" ]

    unset BOTS_EXCLUDED_PROFILE_SOURCE CLAUDE_PROFILE_DIR
    BOTS_PROFILE_DIR="scripts/custom_profile"
    apply_hybrid_profile_defaults start hybrid
    [ "$BOTS_PROFILE_DIR" = "scripts/custom_profile" ]
    [ -z "${BOTS_EXCLUDED_PROFILE_SOURCE:-}" ]
    [ -z "${CLAUDE_PROFILE_DIR:-}" ]

    export HYBRID_USE_CLAUDE_CODE=yes
    unset BOTS_PROFILE_DIR BOTS_EXCLUDED_PROFILE_SOURCE CLAUDE_PROFILE_DIR
    apply_hybrid_profile_defaults start merchant_hybrid
    [ "$BOTS_PROFILE_DIR" = "scripts/4bots_merchant_operations_profile" ]
    [ "$BOTS_EXCLUDED_PROFILE_SOURCE" = "platform-data" ]
    [ "$CLAUDE_PROFILE_DIR" = "scripts/4bots_merchant_operations_profile_for_claude" ]

    unset BOTS_EXCLUDED_PROFILE_SOURCE CLAUDE_PROFILE_DIR
    BOTS_PROFILE_DIR="scripts/custom_profile"
    apply_hybrid_profile_defaults start merchant_hybrid
    [ "$BOTS_PROFILE_DIR" = "scripts/custom_profile" ]
    [ -z "${BOTS_EXCLUDED_PROFILE_SOURCE:-}" ]
    [ -z "${CLAUDE_PROFILE_DIR:-}" ]

    BOTS_PROFILE_DIR="scripts/custom_profile"
    BOTS_EXCLUDED_PROFILE_SOURCE="custom-platform-data"
    CLAUDE_PROFILE_DIR="scripts/custom_claude_profile"
    HYBRID_USE_CLAUDE_CODE="invalid-but-unused-for-explicit-claude"
    apply_hybrid_profile_defaults start hybrid
    [ "$BOTS_PROFILE_DIR" = "scripts/custom_profile" ]
    [ "$BOTS_EXCLUDED_PROFILE_SOURCE" = "custom-platform-data" ]
    [ "$CLAUDE_PROFILE_DIR" = "scripts/custom_claude_profile" ]

    apply_hybrid_profile_defaults start merchant_hybrid
    [ "$BOTS_PROFILE_DIR" = "scripts/custom_profile" ]
    [ "$BOTS_EXCLUDED_PROFILE_SOURCE" = "custom-platform-data" ]
    [ "$CLAUDE_PROFILE_DIR" = "scripts/custom_claude_profile" ]

    unset BOTS_PROFILE_DIR BOTS_EXCLUDED_PROFILE_SOURCE CLAUDE_PROFILE_DIR
    apply_hybrid_profile_defaults status hybrid
    [ -z "${BOTS_PROFILE_DIR:-}" ]
    [ -z "${BOTS_EXCLUDED_PROFILE_SOURCE:-}" ]
    [ -z "${CLAUDE_PROFILE_DIR:-}" ]

    apply_hybrid_profile_defaults status merchant_hybrid
    [ -z "${BOTS_PROFILE_DIR:-}" ]
    [ -z "${BOTS_EXCLUDED_PROFILE_SOURCE:-}" ]
    [ -z "${CLAUDE_PROFILE_DIR:-}" ]
)

test_hybrid_profile_defaults

test_hybrid_restart_restores_previous_selection_without_prompts() (
    log_info() { :; }
    local restart_state="$TMP/hybrid-restart-state.json"
    HYBRID_STATE_FILE="$restart_state"
    jq -n \
        --arg bots_profile "$ROOT/scripts/4bots_merchant_operations_profile" \
        --arg claude_profile "$CLAUDE_PROFILE" \
        '{
          mode: "claude",
          bots_profile_dir: $bots_profile,
          excluded_profile_source: "platform-data",
          claude_profile_dir: $claude_profile,
          claude_config_mode: "env-local",
          anthropic_base_url: "https://saved-anthropic-gateway.example.test",
          singlebox_model_config_mode: "manual"
        }' > "$restart_state"
    HYBRID_PROFILE_OPTIONS_EXPLICIT=0
    HYBRID_RUNTIME_SELECTION_EXPLICIT=0
    unset BOTS_PROFILE_DIR BOTS_EXCLUDED_PROFILE_SOURCE CLAUDE_PROFILE_DIR
    unset HYBRID_CLAUDE_CONFIG_MODE HYBRID_RESTART_FROM_STATE SINGLEBOX_MODEL_CONFIG_MODE
    unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_API_KEY ANTHROPIC_MODEL
    hybrid_confirm_choice() {
        echo 'unexpected restart confirmation prompt' >&2
        return 2
    }
    claude_relay_prompt_anthropic_base_url() {
        echo 'unexpected restart base URL prompt' >&2
        return 2
    }
    claude_relay_find_cli() { printf '%s\n' "$TMP/fake-claude"; }

    apply_hybrid_profile_defaults restart hybrid
    [ "$HYBRID_RESTART_FROM_STATE" = "1" ]
    [ "$BOTS_PROFILE_DIR" = "$ROOT/scripts/4bots_merchant_operations_profile" ]
    [ "$BOTS_EXCLUDED_PROFILE_SOURCE" = "platform-data" ]
    [ "$CLAUDE_PROFILE_DIR" = "$CLAUDE_PROFILE" ]
    [ "$HYBRID_CLAUDE_CONFIG_MODE" = "env-local" ]
    [ "$ANTHROPIC_BASE_URL" = "https://saved-anthropic-gateway.example.test" ]
    [ "$SINGLEBOX_MODEL_CONFIG_MODE" = "manual" ]

    prepare_hybrid_claude_runtime_choice restart hybrid
    [ "$ANTHROPIC_AUTH_TOKEN" = "$OPENCLAW_OPENAI_API_KEY" ]
    [ "$ANTHROPIC_MODEL" = "$OPENCLAW_OPENAI_MODEL_ID" ]
)

test_hybrid_restart_restores_previous_selection_without_prompts

test_hybrid_restart_restores_home_model_confirmation() (
    log_info() { :; }
    HYBRID_STATE_FILE="$TMP/hybrid-restart-home-state.json"
    jq -n \
        --arg bots_profile "$ROOT/scripts/4bots_merchant_operations_profile" \
        '{
          mode: "openclaw",
          bots_profile_dir: $bots_profile,
          excluded_profile_source: "",
          claude_profile_dir: "",
          claude_config_mode: "",
          anthropic_base_url: "",
          singlebox_model_config_mode: "home"
        }' > "$HYBRID_STATE_FILE"
    HYBRID_PROFILE_OPTIONS_EXPLICIT=0
    unset BOTS_PROFILE_DIR BOTS_EXCLUDED_PROFILE_SOURCE CLAUDE_PROFILE_DIR
    unset HYBRID_RESTART_FROM_STATE SINGLEBOX_MODEL_CONFIG_MODE SINGLEBOX_MODEL_CONFIG_HOME_CONFIRMED

    apply_hybrid_profile_defaults restart hybrid
    [ "$SINGLEBOX_MODEL_CONFIG_MODE" = "home" ]
    [ "$SINGLEBOX_MODEL_CONFIG_HOME_CONFIRMED" = "1" ]
)

test_hybrid_restart_restores_home_model_confirmation

test_hybrid_restart_without_state_is_rejected() (
    log_error() { :; }
    HYBRID_STATE_FILE="$TMP/missing-hybrid-restart-state.json"
    HYBRID_PROFILE_OPTIONS_EXPLICIT=0
    unset BOTS_PROFILE_DIR BOTS_EXCLUDED_PROFILE_SOURCE CLAUDE_PROFILE_DIR
    if apply_hybrid_profile_defaults restart hybrid; then
        echo 'hybrid restart without previous state unexpectedly accepted' >&2
        exit 1
    fi
)

test_hybrid_restart_without_state_is_rejected

test_hybrid_claude_runtime_choice() (
    log_info() { :; }
    export BOTS_PROFILE_DIR="$ROOT/scripts/4bots_merchant_operations_profile"
    export BOTS_EXCLUDED_PROFILE_SOURCE="platform-data"
    export CLAUDE_PROFILE_DIR="$CLAUDE_PROFILE"
    claude_relay_find_cli() { printf '%s\n' "$TMP/fake-claude"; }

    hybrid_confirm_choice() {
        echo 'unexpected model configuration prompt' >&2
        return 2
    }

    export SINGLEBOX_MODEL_CONFIG_MODE="home"
    unset HYBRID_CLAUDE_CONFIG_MODE
    prepare_hybrid_claude_runtime_choice start hybrid
    [ "$SINGLEBOX_MODEL_CONFIG_MODE" = "manual" ]
    [ "$HYBRID_CLAUDE_CONFIG_MODE" = "env-local" ]

    export SINGLEBOX_MODEL_CONFIG_MODE="home"
    export HYBRID_CLAUDE_CONFIG_MODE="user"
    prepare_hybrid_claude_runtime_choice start hybrid
    [ "$SINGLEBOX_MODEL_CONFIG_MODE" = "home" ]
    [ "$HYBRID_CLAUDE_CONFIG_MODE" = "user" ]

    export SINGLEBOX_MODEL_CONFIG_MODE="home"
    export HYBRID_CLAUDE_CONFIG_MODE="env-local"
    prepare_hybrid_claude_runtime_choice start merchant_hybrid
    [ "$SINGLEBOX_MODEL_CONFIG_MODE" = "manual" ]
    [ "$HYBRID_CLAUDE_CONFIG_MODE" = "env-local" ]
)

test_hybrid_claude_runtime_choice

test_hybrid_anthropic_defaults_and_base_url_edit() (
    log_warn() { :; }
    export OPENCLAW_OPENAI_BASE_URL="https://openai-upstream.example.test/v1"
    export OPENCLAW_OPENAI_API_KEY="openai-fallback-token"
    export OPENCLAW_OPENAI_MODEL_ID="openai-fallback-model"
    unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_API_KEY ANTHROPIC_MODEL
    local prompt_count=0
    claude_relay_prompt_anthropic_base_url() {
        prompt_count=$((prompt_count + 1))
        ANTHROPIC_BASE_URL="https://edited-anthropic-gateway.example.test"
        export ANTHROPIC_BASE_URL
    }

    claude_relay_prepare_env_local_model
    [ "$prompt_count" = "1" ]
    [ "$ANTHROPIC_BASE_URL" = "https://edited-anthropic-gateway.example.test" ]
    [ "$ANTHROPIC_AUTH_TOKEN" = "openai-fallback-token" ]
    [ "$ANTHROPIC_MODEL" = "openai-fallback-model" ]
)

test_hybrid_anthropic_defaults_and_base_url_edit

test_hybrid_missing_anthropic_env_is_rejected() (
    log_info() { :; }
    log_error() { :; }
    export BOTS_PROFILE_DIR="$ROOT/scripts/4bots_merchant_operations_profile"
    export BOTS_EXCLUDED_PROFILE_SOURCE="platform-data"
    export CLAUDE_PROFILE_DIR="$CLAUDE_PROFILE"
    unset HYBRID_CLAUDE_CONFIG_MODE ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_API_KEY ANTHROPIC_MODEL OPENCLAW_OPENAI_BASE_URL
    claude_relay_find_cli() { printf '%s\n' "$TMP/fake-claude"; }

    if prepare_hybrid_claude_runtime_choice start hybrid; then
        echo 'incomplete Anthropic configuration unexpectedly accepted' >&2
        exit 1
    fi
)

test_hybrid_missing_anthropic_env_is_rejected

test_hybrid_missing_claude_cancels_when_install_declined() (
    log_info() { :; }
    log_error() { :; }
    export BOTS_PROFILE_DIR="$ROOT/scripts/4bots_merchant_operations_profile"
    export BOTS_EXCLUDED_PROFILE_SOURCE="platform-data"
    export CLAUDE_PROFILE_DIR="$CLAUDE_PROFILE"
    export HYBRID_INSTALL_CLAUDE_CODE=no
    export HYBRID_CLAUDE_CONFIG_MODE=user
    claude_relay_find_cli() { return 1; }

    if prepare_hybrid_claude_runtime_choice start hybrid; then
        echo 'missing Claude Code unexpectedly allowed after installation was declined' >&2
        exit 1
    fi
)

test_hybrid_missing_claude_cancels_when_install_declined

test_hybrid_missing_claude_installs_when_accepted() (
    log_info() { :; }
    export BOTS_PROFILE_DIR="$ROOT/scripts/4bots_merchant_operations_profile"
    export BOTS_EXCLUDED_PROFILE_SOURCE="platform-data"
    export CLAUDE_PROFILE_DIR="$CLAUDE_PROFILE"
    export HYBRID_INSTALL_CLAUDE_CODE=yes
    export HYBRID_CLAUDE_CONFIG_MODE=user
    local fake_cli="$TMP/installed-claude"
    claude_relay_find_cli() {
        [ -x "$fake_cli" ] && printf '%s\n' "$fake_cli"
    }
    claude_relay_install_cli() {
        printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$fake_cli"
        chmod +x "$fake_cli"
    }

    prepare_hybrid_claude_runtime_choice start hybrid
    [ -x "$fake_cli" ]
)

test_hybrid_missing_claude_installs_when_accepted

events="$TMP/events"
hybrid_port_preflight() { return 0; }
check_prereqs_for_services() { return 97; }
hybrid_prereqs
check_prereqs_for_services() { return 0; }
print_local_stack_ready_banner() { printf '%s\n' 'ready' >> "$events"; }
for service in claude_relays baas backend bcs bcsfuse bots claude_bots bcs_baas_provider frontend; do
    eval "${service}_setup() { printf '%s\\n' 'setup:${service}' >> \"\$events\"; }"
    eval "${service}_start() { printf '%s\\n' 'start:${service}' >> \"\$events\"; }"
    eval "${service}_stop() { printf '%s\\n' 'stop:${service}' >> \"\$events\"; }"
    eval "${service}_ready() { return 0; }"
    eval "${service}_status() { printf '%s\\n' 'status:${service}' >> \"\$events\"; }"
done

test_hybrid_explicit_selection_switches_active_runtime() (
    HYBRID_STATE_FILE="$TMP/hybrid-transition-state.json"
    HYBRID_RUNTIME_SELECTION_EXPLICIT=0
    export BOTS_PROFILE_DIR="$ROOT/scripts/4bots_merchant_operations_profile"
    unset BOTS_EXCLUDED_PROFILE_SOURCE CLAUDE_PROFILE_DIR HYBRID_CLAUDE_ACTIVE MERCHANT_HYBRID_ACTIVE
    : > "$events"
    hybrid_start
    [[ "$(jq -r '.mode' "$HYBRID_STATE_FILE")" == "openclaw" ]]

    : > "$events"
    HYBRID_RUNTIME_SELECTION_EXPLICIT=1
    export BOTS_EXCLUDED_PROFILE_SOURCE="platform-data"
    export CLAUDE_PROFILE_DIR="$CLAUDE_PROFILE"
    export HYBRID_CLAUDE_CONFIG_MODE="env-local"
    hybrid_prereqs
    expected_transition_stop=$'stop:frontend\nstop:bots\nstop:bcsfuse\nstop:bcs'
    [[ "$(cat "$events")" == "$expected_transition_stop" ]]
    [[ ! -f "$HYBRID_STATE_FILE" ]]
    [[ "$BOTS_EXCLUDED_PROFILE_SOURCE" == "platform-data" ]]
    [[ "$CLAUDE_PROFILE_DIR" == "$CLAUDE_PROFILE" ]]
    [[ "$HYBRID_CLAUDE_CONFIG_MODE" == "env-local" ]]
    [[ "${HYBRID_START_ORDER[*]}" == "${HYBRID_CLAUDE_START_ORDER[*]}" ]]
)

test_hybrid_explicit_selection_switches_active_runtime
: > "$events"

unset BOTS_EXCLUDED_PROFILE_SOURCE CLAUDE_PROFILE_DIR HYBRID_CLAUDE_ACTIVE MERCHANT_HYBRID_ACTIVE
hybrid_start
expected_openclaw_start=$'setup:bcs\nsetup:bcsfuse\nsetup:bots\nsetup:frontend\nstart:bcs\nstart:bcsfuse\nstart:bots\nstart:frontend\nready'
[[ "$(cat "$events")" == "$expected_openclaw_start" ]]
[[ "$(jq -r '.mode' "$HYBRID_STATE_FILE")" == "openclaw" ]]

: > "$events"
hybrid_stop
expected_openclaw_stop=$'stop:frontend\nstop:bots\nstop:bcsfuse\nstop:bcs'
[[ "$(cat "$events")" == "$expected_openclaw_stop" ]]
[[ ! -f "$HYBRID_STATE_FILE" ]]

: > "$events"
export BOTS_EXCLUDED_PROFILE_SOURCE="platform-data"
export CLAUDE_PROFILE_DIR="$CLAUDE_PROFILE"
hybrid_start
expected_start=$'setup:claude_relays\nsetup:baas\nsetup:backend\nsetup:bcs\nsetup:bcsfuse\nsetup:bots\nsetup:claude_bots\nsetup:bcs_baas_provider\nsetup:frontend\nstart:claude_relays\nstart:baas\nstart:backend\nstart:bcs\nstart:bcsfuse\nstart:bots\nstart:claude_bots\nstart:bcs_baas_provider\nstart:frontend\nready'
[[ "$(cat "$events")" == "$expected_start" ]]
[[ "$(jq -r '.mode' "$HYBRID_STATE_FILE")" == "claude" ]]
[[ "$(jq -r '.claude_config_mode' "$HYBRID_STATE_FILE")" == "env-local" ]]
[[ "$(jq -r '.anthropic_base_url' "$HYBRID_STATE_FILE")" == "$ANTHROPIC_BASE_URL" ]]
[[ "$(jq -r '.singlebox_model_config_mode' "$HYBRID_STATE_FILE")" == "$SINGLEBOX_MODEL_CONFIG_MODE" ]]
expected_stop=$'stop:frontend\nstop:bcs_baas_provider\nstop:claude_bots\nstop:bots\nstop:bcsfuse\nstop:bcs\nstop:backend\nstop:baas\nstop:claude_relays'

: > "$events"
unset BOTS_EXCLUDED_PROFILE_SOURCE CLAUDE_PROFILE_DIR
hybrid_status >/dev/null
expected_status=$'status:claude_relays\nstatus:baas\nstatus:backend\nstatus:bcs\nstatus:bcsfuse\nstatus:bots\nstatus:claude_bots\nstatus:bcs_baas_provider\nstatus:frontend'
[[ "$(cat "$events")" == "$expected_status" ]]
[[ "$BOTS_EXCLUDED_PROFILE_SOURCE" == "platform-data" ]]
[[ "$CLAUDE_PROFILE_DIR" == "$CLAUDE_PROFILE" ]]

: > "$events"
unset BOTS_EXCLUDED_PROFILE_SOURCE CLAUDE_PROFILE_DIR
sleep() { :; }
hybrid_restart
expected_restart="${expected_stop}"$'\n'"${expected_start}"
[[ "$(cat "$events")" == "$expected_restart" ]]
[[ "$(jq -r '.mode' "$HYBRID_STATE_FILE")" == "claude" ]]

: > "$events"
unset BOTS_EXCLUDED_PROFILE_SOURCE CLAUDE_PROFILE_DIR
hybrid_stop
[[ "$(cat "$events")" == "$expected_stop" ]]
[[ "$BOTS_EXCLUDED_PROFILE_SOURCE" == "platform-data" ]]
[[ "$CLAUDE_PROFILE_DIR" == "$CLAUDE_PROFILE" ]]
[[ ! -f "$HYBRID_STATE_FILE" ]]

: > "$events"
claude_bots_start() { printf '%s\n' 'start:claude_bots' >> "$events"; return 23; }
if hybrid_start; then
    echo 'hybrid unexpectedly succeeded after Claude bot failure' >&2
    exit 1
fi
expected_rollback=$'setup:claude_relays\nsetup:baas\nsetup:backend\nsetup:bcs\nsetup:bcsfuse\nsetup:bots\nsetup:claude_bots\nsetup:bcs_baas_provider\nsetup:frontend\nstart:claude_relays\nstart:baas\nstart:backend\nstart:bcs\nstart:bcsfuse\nstart:bots\nstart:claude_bots\nstop:bots\nstop:bcsfuse\nstop:bcs\nstop:backend\nstop:baas\nstop:claude_relays'
[[ "$(cat "$events")" == "$expected_rollback" ]]
[[ ! -f "$HYBRID_STATE_FILE" ]]

dispatch_events="$TMP/dispatch-events"
hybrid_prereqs() {
    # Reproduce the common helper's `svc` loop variable. The outer dispatcher
    # must still invoke hybrid rather than the final frontend service.
    local svc
    for svc in claude_relays frontend; do :; done
}
hybrid_start() { printf '%s\n' 'start:hybrid' >> "$dispatch_events"; }
frontend_start() { printf '%s\n' 'start:frontend' >> "$dispatch_events"; }
start_service hybrid
[[ "$(cat "$dispatch_events")" == 'start:hybrid' ]]

: > "$dispatch_events"
start_service merchant_hybrid
[[ "$(cat "$dispatch_events")" == 'start:hybrid' ]]

help_output="$(show_help)"
[[ "$help_output" == *"hybrid - OpenClaw profile stack"* ]]
[[ "$help_output" != *"merchant_hybrid"* ]]

echo 'hybrid profile shell tests passed'
