#!/bin/bash
# Local smoke flow for the 5 OpenClaw bots started by start_bcs_bots.sh.
#
# Prerequisites:
#   1. BCS is running with local auth mock enabled:
#        BCS_AUTH_MOCK=1 ./scripts/start_bcs_bots.sh start
#   2. The 5 bot gateways have connected and written session files.
#   3. HUMAN_USER_ID is set for the real human actor joining the group.
#
# Usage:
#   HUMAN_USER_ID=<user_id> HUMAN_NICK_NAME=<nick_name> ./scripts/test_5bot_human_group.sh all
#   ./scripts/test_5bot_human_group.sh onboard
#   ./scripts/test_5bot_human_group.sh friends
#   ./scripts/test_5bot_human_group.sh group

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BCS_PORT="${BCS_PORT:-21000}"
BCS_HTTP_URL="${BCS_HTTP_URL:-http://localhost:${BCS_PORT}}"
BCS_CLI="${BCS_CLI:-$PROJECT_ROOT/target/debug/bcs-cli}"

HUMAN_USER_ID="${HUMAN_USER_ID:-${BCS_MOCK_USER_ID:-}}"
HUMAN_NICK_NAME="${HUMAN_NICK_NAME:-${BCS_MOCK_USER_NICK_NAME:-}}"
HUMAN_CHANNEL="${HUMAN_CHANNEL:-mock}"
BOT_VISIBILITY="${BOT_VISIBILITY:-protected}"
GROUP_TOPIC="${GROUP_TOPIC:-BCS local 5-bot human smoke}"

BOT_IDS=("CEO" "产品经理" "研发" "验证" "客服")
BOT_PROFILES=("ceo" "product-manager" "engineering" "verification" "customer-service")

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "  ${CYAN}→${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }

usage() {
    cat <<EOF
Usage: $0 {summary|onboard|friends|group|all}

Env:
  BCS_PORT          BCS HTTP port, default: 21000
  HUMAN_USER_ID     required for group/all
  HUMAN_NICK_NAME   optional display name for mock human
  BOT_VISIBILITY    visibility set after onboard, default: protected
  GROUP_TOPIC       group topic, default: ${GROUP_TOPIC}

Typical:
  BCS_AUTH_MOCK=1 ./scripts/start_bcs_bots.sh start
  HUMAN_USER_ID=<user_id> HUMAN_NICK_NAME=<nick_name> ./scripts/test_5bot_human_group.sh all
EOF
}

require_command() {
    local command="$1"
    if ! command -v "$command" >/dev/null 2>&1; then
        fail "Missing command: $command"
        exit 1
    fi
}

require_bcs() {
    if ! curl --noproxy '*' -sS "${BCS_HTTP_URL}/health" >/dev/null 2>&1; then
        fail "BCS is not healthy at ${BCS_HTTP_URL}"
        exit 1
    fi
}

require_cli() {
    if [ ! -x "$BCS_CLI" ]; then
        fail "bcs-cli not found or not executable: $BCS_CLI"
        echo "  Run: cargo build --package bcs-cli"
        exit 1
    fi
}

session_file_for_profile() {
    local profile="$1"
    echo "$HOME/.openclaw-${profile}/.bcs/session.json"
}

json_file_field() {
    local file="$1"
    local field="$2"
    python3 - "$file" "$field" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
value = data.get(sys.argv[2], "")
print(value if value is not None else "")
PY
}

pending_request_ids_from() {
    local from_bot="$1"
    python3 - "$from_bot" <<'PY'
import json
import sys

from_bot = sys.argv[1]
raw = sys.stdin.read().strip()
if not raw:
    sys.exit(0)
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    sys.exit(0)
for item in data if isinstance(data, list) else []:
    if item.get("from_bot") == from_bot and item.get("status") == "pending":
        request_id = item.get("id")
        if request_id:
            print(request_id)
PY
}

friends_contains() {
    local target_bot="$1"
    python3 - "$target_bot" <<'PY'
import json
import sys

target = sys.argv[1]
data = json.load(sys.stdin)
for item in data if isinstance(data, list) else []:
    if item.get("bot_uuid") == target:
        sys.exit(0)
sys.exit(1)
PY
}

human_headers=()
build_human_headers() {
    if [ -z "$HUMAN_USER_ID" ]; then
        fail "HUMAN_USER_ID is required for human group join"
        exit 1
    fi

    human_headers=(
        -H "Content-Type: application/json"
        -H "X-Mock-User-Id: ${HUMAN_USER_ID}"
        -H "X-Mock-Channel: ${HUMAN_CHANNEL}"
    )
    if [ -n "$HUMAN_NICK_NAME" ]; then
        human_headers+=(-H "X-Mock-Nick-Name: ${HUMAN_NICK_NAME}")
    fi
}

http_json() {
    local method="$1"
    local url="$2"
    local body="${3:-}"
    local tmp
    tmp="$(mktemp)"

    local status
    if [ -n "$body" ]; then
        status=$(curl --noproxy '*' -sS -o "$tmp" -w "%{http_code}" -X "$method" "$url" "${human_headers[@]}" -d "$body")
    else
        status=$(curl --noproxy '*' -sS -o "$tmp" -w "%{http_code}" -X "$method" "$url" "${human_headers[@]}")
    fi

    if [[ "$status" != 2* ]]; then
        fail "$method $url failed with HTTP $status"
        cat "$tmp"
        echo ""
        rm -f "$tmp"
        exit 1
    fi

    cat "$tmp"
    rm -f "$tmp"
}

load_bot_sessions() {
    BOT_UUIDS=()
    BOT_TOKENS=()

    for i in "${!BOT_PROFILES[@]}"; do
        local profile="${BOT_PROFILES[$i]}"
        local bot_id="${BOT_IDS[$i]}"
        local session_file
        session_file="$(session_file_for_profile "$profile")"

        if [ ! -f "$session_file" ]; then
            fail "Missing session for ${bot_id}: $session_file"
            echo "  Start the 5 bots first: BCS_AUTH_MOCK=1 ./scripts/start_bcs_bots.sh start"
            exit 1
        fi

        local uuid token
        uuid="$(json_file_field "$session_file" bot_uuid)"
        token="$(json_file_field "$session_file" token)"
        if [ -z "$uuid" ] || [ -z "$token" ]; then
            fail "Invalid session for ${bot_id}: $session_file"
            exit 1
        fi

        BOT_UUIDS+=("$uuid")
        BOT_TOKENS+=("$token")
    done
}

run_cli() {
    "$BCS_CLI" --url "$BCS_HTTP_URL" --json "$@"
}

cmd_summary() {
    require_command curl
    require_command python3
    require_bcs
    require_cli
    load_bot_sessions

    echo ""
    info "BCS: ${BCS_HTTP_URL}"
    info "5 bot sessions:"
    for i in "${!BOT_IDS[@]}"; do
        pass "${BOT_IDS[$i]} profile=${BOT_PROFILES[$i]} uuid=${BOT_UUIDS[$i]}"
    done
}

cmd_onboard() {
    require_bcs
    require_cli
    info "Running existing 5-bot onboard command..."
    bash "$SCRIPT_DIR/start_bcs_bots.sh" onboard

    load_bot_sessions
    info "Setting bot visibility to ${BOT_VISIBILITY}..."
    for i in "${!BOT_IDS[@]}"; do
        run_cli visibility --token "${BOT_TOKENS[$i]}" set \
            --value "$BOT_VISIBILITY" \
            --bot-uuid "${BOT_UUIDS[$i]}" >/dev/null
        pass "${BOT_IDS[$i]} visibility=${BOT_VISIBILITY}"
    done
}

cmd_friends() {
    require_bcs
    require_cli
    load_bot_sessions

    local driver_uuid="${BOT_UUIDS[0]}"
    local driver_token="${BOT_TOKENS[0]}"
    info "Creating friendships from driver ${BOT_IDS[0]} (${driver_uuid}) to other 4 bots..."

    for i in 1 2 3 4; do
        local target_uuid="${BOT_UUIDS[$i]}"
        local target_token="${BOT_TOKENS[$i]}"

        info "Request: ${BOT_IDS[0]} -> ${BOT_IDS[$i]}"
        run_cli friend --token "$driver_token" request --bot-uuid "$target_uuid" >/dev/null

        local pending request_ids
        pending="$(run_cli friend --token "$target_token" requests --direction received --status pending)"
        request_ids="$(echo "$pending" | pending_request_ids_from "$driver_uuid")"

        if [ -n "$request_ids" ]; then
            while IFS= read -r request_id; do
                [ -z "$request_id" ] && continue
                run_cli friend --token "$target_token" accept --request-id "$request_id" >/dev/null
                pass "${BOT_IDS[$i]} accepted request ${request_id}"
            done <<< "$request_ids"
        else
            warn "No pending request for ${BOT_IDS[$i]} from driver; may already be friends"
        fi

        if run_cli friend --token "$driver_token" list --bot-uuid "$driver_uuid" | friends_contains "$target_uuid"; then
            pass "Friendship verified: ${BOT_IDS[0]} <-> ${BOT_IDS[$i]}"
        else
            fail "Friendship missing: ${BOT_IDS[0]} <-> ${BOT_IDS[$i]}"
            exit 1
        fi
    done
}

cmd_group() {
    require_command curl
    require_command python3
    require_bcs
    require_cli
    build_human_headers
    load_bot_sessions

    local driver_uuid="${BOT_UUIDS[0]}"
    local driver_token="${BOT_TOKENS[0]}"
    local participants
    participants="$(IFS=,; echo "${BOT_UUIDS[*]:1}")"

    info "Ensuring human actor human_${HUMAN_USER_ID}..."
    http_json POST "${BCS_HTTP_URL}/me/ensure-human" '{}' >/dev/null

    info "Creating 5-bot group..."
    local create_output group_id
    create_output="$(run_cli create-group \
        --token "$driver_token" \
        --driver "$driver_uuid" \
        --participants "$participants" \
        --topic "$GROUP_TOPIC")"
    group_id="$(echo "$create_output" | awk -F': ' '/^[[:space:]]*ID:/ {print $2; exit}')"

    if [ -z "$group_id" ]; then
        fail "Cannot parse group id from create-group output"
        echo "$create_output"
        exit 1
    fi
    pass "Group created: ${group_id}"

    info "Human self-joining group as present..."
    http_json PUT "${BCS_HTTP_URL}/groups/${group_id}/participants/human_${HUMAN_USER_ID}/mode" '{"mode":"present"}' >/dev/null
    pass "Human joined: human_${HUMAN_USER_ID}"

    info "Verifying group detail..."
    local group_json
    group_json="$(run_cli get-group --token "$driver_token" --id "$group_id")"
    echo "$group_json" | grep -q "human_${HUMAN_USER_ID}" || {
        fail "Human participant not found in group detail"
        echo "$group_json"
        exit 1
    }
    pass "Group contains 5 bots + human_${HUMAN_USER_ID}"
    echo "$group_json"
}

main() {
    local command="${1:-all}"
    case "$command" in
        summary) cmd_summary ;;
        onboard) cmd_onboard ;;
        friends) cmd_friends ;;
        group) cmd_group ;;
        all)
            cmd_onboard
            cmd_friends
            cmd_group
            ;;
        -h|--help|help) usage ;;
        *)
            usage
            exit 1
            ;;
    esac
}

main "$@"
