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
bots_dynamic_copy_profile_files merchant-operations "$MANAGER_WORKSPACE"
bots_dynamic_setup_bcs_skill "$MANAGER_WORKSPACE"
for reference in \
    'skills/bcs-coordination/SKILL.md' \
    'skills/bcs-coordination/references/custom-collaboration.md' \
    'skills/bcs-coordination/references/custom-collaboration-schema.md'; do
    [[ -f "$MANAGER_WORKSPACE/$reference" ]]
done
grep -Fq 'skills/bcs-coordination/references/custom-collaboration.md' "$MANAGER_WORKSPACE/AGENTS.md"
grep -Fq 'skills/bcs-coordination/references/custom-collaboration-schema.md' "$MANAGER_WORKSPACE/TOOLS.md"

IFS=$'\x1f' read -r role _ _ port config_dir workspace model prompt permission < <(claude_profile_entries)
[[ "$role" == "platform-data" ]]
[[ "$port" == "18900" ]]
[[ "$model" == "Kimi-K2.6" ]]
[[ "$permission" == "bypassPermissions" ]]
[[ "$config_dir" == "$TMP/claude-config" ]]
[[ "$workspace" == "$TMP/claude-workspace" ]]
[[ "$prompt" == "$CLAUDE_PROFILE/platform-data/CLAUDE.md" ]]
grep -Fq 'AskUserQuestion' "$CLAUDE_PROFILE/platform-data/CLAUDE.md"

MODEL_CONFIG="$TMP/model-config.json"
cat > "$MODEL_CONFIG" <<'JSON'
{
  "models": {
    "providers": {
      "antchat": {
        "models": [
          {"id": "Kimi-K2.5", "name": "Kimi-K2.5", "input": ["text"]}
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {"primary": "antchat/Kimi-K2.5"},
      "models": {"antchat/Kimi-K2.5": {"alias": "Kimi-K2.5"}}
    }
  }
}
JSON
export SINGLEBOX_MODEL_CONFIG_FILE="$MODEL_CONFIG"
hybrid_apply_model_policy
jq -e '
  .agents.defaults.model.primary == "antchat/Kimi-K2.6"
  and .agents.defaults.models["antchat/Kimi-K2.6"].alias == "Kimi-K2.6"
  and ([.models.providers.antchat.models[].id] | index("Kimi-K2.6")) != null
' "$MODEL_CONFIG" >/dev/null
[[ "$SINGLEBOX_REQUIRED_OPENCLAW_MODEL" == "antchat/Kimi-K2.6" ]]
[[ "$LLM_FAST_MODEL" == "Kimi-K2.6" ]]
[[ "$LLM_BALANCED_MODEL" == "Kimi-K2.6" ]]
[[ "$LLM_REASONING_MODEL" == "Kimi-K2.6" ]]
[[ "$LLM_LONG_CONTEXT_MODEL" == "Kimi-K2.6" ]]
[[ "$LLM_EXTRACTION_MODEL" == "Kimi-K2.6" ]]

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
[[ "$LLM_FAST_MODEL" == "Kimi-K2.6" ]]
[[ "$LLM_BALANCED_MODEL" == "Kimi-K2.6" ]]
[[ "$LLM_REASONING_MODEL" == "Kimi-K2.6" ]]
[[ "$LLM_LONG_CONTEXT_MODEL" == "Kimi-K2.6" ]]
[[ "$LLM_EXTRACTION_MODEL" == "Kimi-K2.6" ]]

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
