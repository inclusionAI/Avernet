#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
CONFIG="$TMP/claude-bots.json"

python3 - "$CONFIG" <<'PY'
import json
import sys

roles = [("planner", 18910), ("developer", 18911), ("reviewer", 18912)]
json.dump({"bots": [{"role": role, "relay_port": port, "claude_config_dir": f"/tmp/{role}/config", "workspace": f"/tmp/{role}/workspace"} for role, port in roles]}, open(sys.argv[1], "w"))
PY

CLAUDE_BOTS_CONFIG="$CONFIG"
# shellcheck source=/dev/null
source "$ROOT/scripts/singlebox.sh"
claude_bots_validate_config
BCS_MOCK_USER_ID="001"
[[ "$(bcs_baas_provider_bcs_owner_id)" == "001" ]]
all_select_topology
[[ " ${START_ORDER[*]} " == *" bots claude_bots bcs_baas_provider frontend "* ]]
[[ " ${START_ORDER[*]} " != *" demo_bot "* ]]
[[ "${#START_ORDER[@]}" -eq 9 ]]

test_provider_registration_separates_backend_and_bcs_owners() {
    local provider_tmp calls
    provider_tmp="$TMP/provider-registration"
    calls="$provider_tmp/calls"
    mkdir -p "$provider_tmp"

    CLAUDE_BOTS_STATE_FILE="$provider_tmp/claude_bots.state.json"
    BCS_BAAS_PROVIDER_STATE_FILE="$provider_tmp/provider.state.json"
    BCS_BAAS_PROVIDER_TOKEN_FILE="$provider_tmp/provider.tokens.json"
    LOG_DIR="$provider_tmp"
    BCS_PORT=21000
    BCS_MOCK_USER_ID=001
    printf '%s\n' '{"entity_id":"mock-user","bots":[{"role":"planner","bot_id":"planner-backend","name":"Claude Planner"},{"role":"developer","bot_id":"developer-backend","name":"Claude Developer"},{"role":"reviewer","bot_id":"reviewer-backend","name":"Claude Reviewer"}]}' > "$CLAUDE_BOTS_STATE_FILE"
    printf '%s\n' '{"baas_token":"test","provider_bots":{}}' > "$BCS_BAAS_PROVIDER_TOKEN_FILE"

    curl() {
        local arg method='' url='' data='' raw_args="$*"
        while [ "$#" -gt 0 ]; do
            arg="$1"
            case "$arg" in
                -X) method="$2"; shift 2 ;;
                -d) data="$2"; shift 2 ;;
                http://*) url="$arg"; shift ;;
                *) shift ;;
            esac
        done
        printf '%s|%s|%s|%s\n' "$method" "$url" "$data" "$raw_args" >> "$calls"
        case "$url" in
            */providers)
                printf '%s\n' '{"provider_id":"provider-test","provider_admin_token":"admin-test","bcs_to_provider_token":"bcs-to-provider-test"}'
                ;;
            */bots)
                case "$data" in
                    *planner*) printf '%s\n' '{"bot_uuid":"provider-planner","bot_runtime_token":"planner-token"}' ;;
                    *developer*) printf '%s\n' '{"bot_uuid":"provider-developer","bot_runtime_token":"developer-token"}' ;;
                    *reviewer*) printf '%s\n' '{"bot_uuid":"provider-reviewer","bot_runtime_token":"reviewer-token"}' ;;
                    *) return 1 ;;
                esac
                ;;
            */visibility)
                printf '%s\n' '{"success":true,"data":{"visibility":"public"}}'
                ;;
            *) return 1 ;;
        esac
    }

    bcs_baas_provider_register
    unset -f curl

    jq -e '.bcs_owner_id == "001" and (.bots | length == 3)' "$BCS_BAAS_PROVIDER_STATE_FILE" >/dev/null
    jq -e '.bcs_to_provider_token == "bcs-to-provider-test" and .provider_admin_token == "admin-test" and (.provider_bots.planner | keys == ["provider_bot_ref"])' "$BCS_BAAS_PROVIDER_TOKEN_FILE" >/dev/null
    grep -F 'X-Mock-User-Id: 001' "$calls" >/dev/null
    grep -F 'planner-backend:mock-user' "$calls" >/dev/null
    grep -F 'Claude Developer（当前）' "$calls" >/dev/null
    grep -F '"owners"' "$calls" >/dev/null
    [[ "$(grep -c '^PUT|.*\/visibility|' "$calls")" -eq 3 ]]
}

test_provider_registration_separates_backend_and_bcs_owners

test_provider_cleanup_removes_only_current_bots() {
    local provider_tmp calls
    provider_tmp="$TMP/provider-cleanup"
    calls="$provider_tmp/calls"
    mkdir -p "$provider_tmp"

    BCS_BAAS_PROVIDER_STATE_FILE="$provider_tmp/provider.state.json"
    BCS_BAAS_PROVIDER_TOKEN_FILE="$provider_tmp/provider.tokens.json"
    BCS_PORT=21000
    printf '%s\n' '{"provider_id":"provider-test","bots":[{"role":"planner","provider_bot_ref":"planner-backend:mock-user"},{"role":"developer","provider_bot_ref":"developer-backend:mock-user"},{"role":"reviewer","provider_bot_ref":"reviewer-backend:mock-user"}]}' > "$BCS_BAAS_PROVIDER_STATE_FILE"
    printf '%s\n' '{"baas_token":"test","provider_admin_token":"admin-test","bcs_to_provider_token":"bcs-to-provider-test","provider_bots":{"planner":{"provider_bot_ref":"planner-backend:mock-user"},"developer":{"provider_bot_ref":"developer-backend:mock-user"},"reviewer":{"provider_bot_ref":"reviewer-backend:mock-user"}}}' > "$BCS_BAAS_PROVIDER_TOKEN_FILE"

    curl() {
        local arg method='' url=''
        while [ "$#" -gt 0 ]; do
            arg="$1"
            case "$arg" in
                -X) method="$2"; shift 2 ;;
                http://*) url="$arg"; shift ;;
                *) shift ;;
            esac
        done
        printf '%s|%s\n' "$method" "$url" >> "$calls"
        printf '%s\n' '{"deleted":true}'
    }

    bcs_baas_provider_cleanup_registration
    unset -f curl

    [ ! -f "$BCS_BAAS_PROVIDER_STATE_FILE" ]
    jq -e '.provider_admin_token == "" and .bcs_to_provider_token == "" and .provider_bots == {} and .baas_token == "test"' "$BCS_BAAS_PROVIDER_TOKEN_FILE" >/dev/null
    [[ "$(grep -c '^DELETE|' "$calls")" -eq 3 ]]
    grep -F 'planner-backend%3Amock-user' "$calls" >/dev/null
}

test_provider_cleanup_removes_only_current_bots

BCS_CONFIG_DIR="$TMP/bcs-runtime-config"
prepare_bcs_runtime_config
grep -F 'block_private_networks = true' "$BCS_CONFIG_DIR/bcs-config.toml" >/dev/null
grep -F 'allow_loopback = true' "$BCS_CONFIG_DIR/bcs-config.toml" >/dev/null

events="$TMP/mixed-rollbacks"
check_prereqs_for_services() { return 0; }
all_mixed_port_ownership_preflight() { return 0; }
print_local_stack_ready_banner() { return 0; }
claude_relays_start() { printf '%s\n' 'start:claude_relays' >> "$events"; }
baas_start() { printf '%s\n' 'start:baas' >> "$events"; }
backend_start() { printf '%s\n' 'start:backend' >> "$events"; }
bcs_start() { printf '%s\n' 'start:bcs' >> "$events"; }
bcsfuse_start() { printf '%s\n' 'start:bcsfuse' >> "$events"; }
bots_start() { printf '%s\n' 'start:bots' >> "$events"; }
claude_bots_start() { printf '%s\n' 'start:claude_bots' >> "$events"; return 23; }
bcs_baas_provider_start() { printf '%s\n' 'start:bcs_baas_provider' >> "$events"; }
frontend_start() { printf '%s\n' 'start:frontend' >> "$events"; }
claude_relays_stop() { printf '%s\n' 'stop:claude_relays' >> "$events"; }
baas_stop() { printf '%s\n' 'stop:baas' >> "$events"; }
backend_stop() { printf '%s\n' 'stop:backend' >> "$events"; }
bcs_stop() { printf '%s\n' 'stop:bcs' >> "$events"; }
bcsfuse_stop() { printf '%s\n' 'stop:bcsfuse' >> "$events"; }
bots_stop() { printf '%s\n' 'stop:bots' >> "$events"; }
claude_bots_stop() { printf '%s\n' 'stop:claude_bots' >> "$events"; }
bcs_baas_provider_stop() { printf '%s\n' 'stop:bcs_baas_provider' >> "$events"; }
frontend_stop() { printf '%s\n' 'stop:frontend' >> "$events"; }

if all_start; then
    echo "mixed all_start unexpectedly succeeded" >&2
    exit 1
fi
expected=$'start:claude_relays\nstart:baas\nstart:backend\nstart:bcs\nstart:bcsfuse\nstart:bots\nstart:claude_bots\nstop:bots\nstop:bcsfuse\nstop:bcs\nstop:backend\nstop:baas\nstop:claude_relays'
[[ "$(cat "$events")" == "$expected" ]]

: > "$events"
check_prereqs_for_services() { return 1; }
all_stop() { printf '%s\n' 'stop:all' >> "$events"; }
all_start() { printf '%s\n' 'start:all' >> "$events"; }
if all_restart; then
    echo "mixed all_restart unexpectedly bypassed failed preflight" >&2
    exit 1
fi
[[ ! -s "$events" ]]

: > "$events"
check_prereqs_for_services() { return 0; }
all_mixed_port_ownership_preflight() { return 0; }
all_stop() { printf '%s\n' 'stop:all' >> "$events"; }
all_start() { printf '%s\n' 'start:all' >> "$events"; }
singlebox_mock_model_stop() { printf '%s\n' 'mock:stop' >> "$events"; }
singlebox_mock_model_start() { printf '%s\n' 'mock:start' >> "$events"; SINGLEBOX_MOCK_MODEL_STARTED_BY_COMMAND=0; }
sleep() { :; }
all_restart
[[ "$(cat "$events")" == $'stop:all\nmock:stop\nmock:start\nstart:all' ]]

: > "$events"
ensure_git_hooks_installed() { :; }
show_standalone_mode_info() { :; }
singlebox_model_config_required_for_services() { return 1; }
all_restart() { printf '%s\n' 'restart:all' >> "$events"; }
main --standalone --claude-bots-config "$CONFIG" restart all >/dev/null
[[ "$(cat "$events")" == 'restart:all' ]]

python3 - "$CONFIG" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path))
data["bots"][0]["relay_port"] = 18913
json.dump(data, open(path, "w"))
PY
if claude_bots_validate_config; then
    echo "invalid fixed role port unexpectedly accepted" >&2
    exit 1
fi

python3 - "$CONFIG" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path))
data["bots"][0]["relay_port"] = 18910
data["bots"][0]["name"] = "planner\tunsafe"
json.dump(data, open(path, "w"))
PY
if claude_bots_validate_config; then
    echo "tab-delimited Claude bot config unexpectedly accepted" >&2
    exit 1
fi

python3 - "$CONFIG" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path))
data["bots"][0]["name"] = "Claude Planner"
data["bots"][1]["workspace"] = data["bots"][0]["workspace"]
json.dump(data, open(path, "w"))
PY
if claude_bots_validate_config; then
    echo "shared Claude workspace unexpectedly accepted" >&2
    exit 1
fi

python3 - "$CONFIG" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path))
data["bots"][1]["workspace"] = "/tmp/developer/workspace"
data["bots"][1]["name"] = "Claude Planner"
json.dump(data, open(path, "w"))
PY
if claude_bots_validate_config; then
    echo "duplicate Claude bot name unexpectedly accepted" >&2
    exit 1
fi

echo "singlebox mixed Claude bot shell tests passed"
