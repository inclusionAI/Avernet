#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"

cleanup() {
    rm -rf "$TMP"
}
trap cleanup EXIT

export HOME="$TMP/home"
mkdir -p "$HOME/.claude"
printf '%s\n' '{"env":{"ANTHROPIC_MODEL":"test-model"}}' > "$HOME/.claude/settings.json"

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
# shellcheck source=/dev/null
source "$ROOT/scripts/singlebox.sh"

merchant_hybrid_validate_profiles
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
merchant_hybrid_apply_model_policy
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
unset MERCHANT_HYBRID_ACTIVE
bcsfuse_load_env
[[ "$LLM_FAST_MODEL" == "runtime-override" ]]
[[ "$LLM_BALANCED_MODEL" == "runtime-override" ]]
[[ "$LLM_REASONING_MODEL" == "runtime-override" ]]
[[ "$LLM_LONG_CONTEXT_MODEL" == "runtime-override" ]]
[[ "$LLM_EXTRACTION_MODEL" == "runtime-override" ]]

export MERCHANT_HYBRID_ACTIVE=1
bcsfuse_load_env
[[ "$LLM_FAST_MODEL" == "Kimi-K2.6" ]]
[[ "$LLM_BALANCED_MODEL" == "Kimi-K2.6" ]]
[[ "$LLM_REASONING_MODEL" == "Kimi-K2.6" ]]
[[ "$LLM_LONG_CONTEXT_MODEL" == "Kimi-K2.6" ]]
[[ "$LLM_EXTRACTION_MODEL" == "Kimi-K2.6" ]]

unset BOTS_EXCLUDED_PROFILE_SOURCE
[[ "$(bots_dynamic_count)" == "4" ]]
export BOTS_EXCLUDED_PROFILE_SOURCE="platform-data"

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
if merchant_hybrid_validate_profiles; then
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
check_prereqs_for_services() { return 0; }
merchant_hybrid_port_preflight() { return 0; }
print_local_stack_ready_banner() { printf '%s\n' 'ready' >> "$events"; }
for service in claude_relays baas backend bcs bcsfuse bots claude_bots bcs_baas_provider frontend; do
    eval "${service}_start() { printf '%s\\n' 'start:${service}' >> \"\$events\"; }"
    eval "${service}_stop() { printf '%s\\n' 'stop:${service}' >> \"\$events\"; }"
    eval "${service}_ready() { return 0; }"
done

merchant_hybrid_start
expected_start=$'start:claude_relays\nstart:baas\nstart:backend\nstart:bcs\nstart:bcsfuse\nstart:bots\nstart:claude_bots\nstart:bcs_baas_provider\nstart:frontend\nready'
[[ "$(cat "$events")" == "$expected_start" ]]

: > "$events"
merchant_hybrid_stop
expected_stop=$'stop:frontend\nstop:bcs_baas_provider\nstop:claude_bots\nstop:bots\nstop:bcsfuse\nstop:bcs\nstop:backend\nstop:baas\nstop:claude_relays'
[[ "$(cat "$events")" == "$expected_stop" ]]

: > "$events"
claude_bots_start() { printf '%s\n' 'start:claude_bots' >> "$events"; return 23; }
if merchant_hybrid_start; then
    echo 'merchant_hybrid unexpectedly succeeded after Claude bot failure' >&2
    exit 1
fi
expected_rollback=$'start:claude_relays\nstart:baas\nstart:backend\nstart:bcs\nstart:bcsfuse\nstart:bots\nstart:claude_bots\nstop:bots\nstop:bcsfuse\nstop:bcs\nstop:backend\nstop:baas\nstop:claude_relays'
[[ "$(cat "$events")" == "$expected_rollback" ]]

dispatch_events="$TMP/dispatch-events"
merchant_hybrid_prereqs() {
    # Reproduce the common helper's `svc` loop variable. The outer dispatcher
    # must still invoke merchant_hybrid rather than the final frontend service.
    local svc
    for svc in claude_relays frontend; do :; done
}
merchant_hybrid_start() { printf '%s\n' 'start:merchant_hybrid' >> "$dispatch_events"; }
frontend_start() { printf '%s\n' 'start:frontend' >> "$dispatch_events"; }
start_service merchant_hybrid
[[ "$(cat "$dispatch_events")" == 'start:merchant_hybrid' ]]

echo 'merchant_hybrid dual-profile shell tests passed'
