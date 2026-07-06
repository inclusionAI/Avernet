#!/bin/bash
# ============================================================================
# Structured Routing E2E Test - Setup
#
# Starts BCS + 3 OpenClaw gateway instances with BCN plugin.
# Follows start_three_openclaw.sh patterns.
#
# Outputs KEY=VALUE lines on stdout for the runner to eval.
# All progress messages go to stderr.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

BCS_BIN="$PROJECT_ROOT/target/debug/bcs"
BCS_CLI="$PROJECT_ROOT/target/debug/bcs-cli"

OPENCLAW_OPENAI_BASE_URL="${OPENCLAW_OPENAI_BASE_URL:?Set OPENCLAW_OPENAI_BASE_URL to the model API base URL}"
OPENCLAW_OPENAI_API_KEY="${OPENCLAW_OPENAI_API_KEY:?Set OPENCLAW_OPENAI_API_KEY to the model API key}"
OPENCLAW_OPENAI_MODEL_ID="${OPENCLAW_OPENAI_MODEL_ID:?Set OPENCLAW_OPENAI_MODEL_ID to the model id}"
OPENCLAW_OPENAI_PROVIDER_ID="${OPENCLAW_OPENAI_PROVIDER_ID:-openai_compatible}"

# Temp dir for BCS data + logs
BOTS_DIR="$(mktemp -d)"
LOG_DIR="$BOTS_DIR/logs"
CONFIG_DIR="$BOTS_DIR/configs"
mkdir -p "$LOG_DIR" "$CONFIG_DIR"

BCS_LOG="$LOG_DIR/bcs.log"
BCS_PID_FILE="$BOTS_DIR/bcs.pid"

# Bot profiles (will be cleaned up by teardown)
COORDINATOR_PROFILE="bcs_test_coordinator"
DBA_PROFILE="bcs_test_dba"
DEVOPS_PROFILE="bcs_test_devops"

# ── Helpers ────────────────────────────────────────────────────────────────

pass() { echo -e "  \033[0;32m✓\033[0m $1" >&2; }
fail() { echo -e "  \033[0;31m✗\033[0m $1" >&2; exit 1; }
info() { echo -e "  \033[0;36m→\033[0m $1" >&2; }

# ── Step 1: Build BCS ─────────────────────────────────────────────────────

info "Building BCS + bcs-cli..."
cargo build --package bcs --package bcs-cli --manifest-path "$PROJECT_ROOT/Cargo.toml" 2>&1 | tail -1 >&2
pass "BCS built"

# ── Step 2: Fixed ports ───────────────────────────────────────────────────
# Use fixed ports so logs/debugging are consistent across runs.
# Override via env: BCS_PORT=21200 bash run.sh

BCS_PORT="${BCS_PORT:-21000}"
COORD_PORT="${COORD_PORT:-21200}"
DBA_PORT="${DBA_PORT:-21300}"
DEVOPS_PORT="${DEVOPS_PORT:-21400}"

# ── Step 3: Write minimal BCS config ──────────────────────────────────────

# ── Step 3: Stage configs (use project configs/ as base, overlay test paths) ──

# Strategy: copy the project's configs/ directory wholesale so we inherit the
# real `bcs-config.toml` + `bcs-config-local.toml` (which already enables
# local SQLite database config + auth_sdk). Then write a tiny overlay file
# that only patches what's specific to this test run: port, bots_base_dir,
# log paths. The config loader deep-merges base + env overlay, so the
# overlay only needs to carry the diffs.
PROJECT_CONFIGS="$PROJECT_ROOT/configs"
if [ ! -d "$PROJECT_CONFIGS" ]; then
    fail "Project configs not found: $PROJECT_CONFIGS"
fi
cp -R "$PROJECT_CONFIGS"/. "$CONFIG_DIR"/

# Strip original `outputs = []` from the logging section copied from the
# project's bcs-config-local.toml. setup.sh appends its own
# [[logging.outputs]] entries below; mixing `outputs = []` + array-of-table
# syntax in the same TOML document is ambiguous and may cause parse errors.
sed -i '' '/^outputs = \[\]/d' "$CONFIG_DIR/bcs-config-local.toml" 2>/dev/null || true

# Append run-local patches to the local overlay so deep_merge picks them up.
# Note: we DON'T overwrite the file; we append, then re-write a clean copy
# that includes the original local config plus our patch fields. This keeps
# the original keys (database / auth_sdk) intact.
# Pre-compute the test artifacts so api_keys can be wired into the
# config before BCS starts. The test seeds a deterministic group_id
# so the key's bound_groups can pin it.
BCS_TEST_SERVICE_GROUP_ID="${BCS_TEST_SERVICE_GROUP_ID:-svc-grp-mw-test}"
# Raw key comes from the environment; production secrets should live in
# encrypted storage.
BCS_TEST_SERVICE_API_KEY="${BCS_TEST_SERVICE_API_KEY:?Set BCS_TEST_SERVICE_API_KEY for the structured routing service API key}"
BCS_TEST_SERVICE_API_KEY_SHA256="$(printf '%s' "$BCS_TEST_SERVICE_API_KEY" | shasum -a 256 | awk '{print $1}')"

cat >> "$CONFIG_DIR/bcs-config-local.toml" <<EOF

# === run-local patches injected by setup.sh ===================================
bind = "127.0.0.1"
port = ${BCS_PORT}
bots_base_dir = "${BOTS_DIR}"
store_messages = true
max_groups_as_driver = 10
max_group_members = 10
max_groups_as_member = 20
max_group_messages = 100
group_chat_delay_min_ms = 0
group_chat_delay_max_ms = 0
strict_container_validation = false

# Part B Task 3/4: seed a service api_key for the
# /services/{group_id}/invocations* test surface. Raw key passed via
# X-BCS-Service-Key by the test client; sha256 below.
[[api_keys]]
name = "bcs-test-service-key"
sha256 = "${BCS_TEST_SERVICE_API_KEY_SHA256}"
bound_groups = ["${BCS_TEST_SERVICE_GROUP_ID}"]

[[logging.outputs]]
name = "main"
path = "${LOG_DIR}"
file = "bcs-main.log"
level = "info"
rotation = "daily"
targets = []
max_keep_days = 1

[[logging.outputs]]
name = "messages"
path = "${LOG_DIR}"
file = "bcs-messages.log"
level = "info"
rotation = "daily"
targets = ["bcs_message"]
max_keep_days = 1

[[logging.outputs]]
name = "group-messages"
path = "${LOG_DIR}"
file = "group-messages.log"
level = "info"
rotation = "daily"
targets = ["ding_group_message"]
max_keep_days = 1
EOF

# ── Step 4: Start BCS ─────────────────────────────────────────────────────

info "Starting BCS on port $BCS_PORT..."
BCS_CONFIG_DIR="$CONFIG_DIR" \
    BCS_DATA_DIR="$BOTS_DIR" \
    RUST_LOG=info \
    BCS_AUTH_MOCK="${BCS_AUTH_MOCK:-1}" \
    BCS_MOCK_USER_ID="${BCS_MOCK_USER_ID:-11111111}" \
    BCS_MOCK_USER_NICK_NAME="${BCS_MOCK_USER_NICK_NAME:-LocalDev}" \
    "$BCS_BIN" > "$BCS_LOG" 2>&1 &
echo $! > "$BCS_PID_FILE"

for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:${BCS_PORT}/health" > /dev/null 2>&1; then
        pass "BCS healthy on port $BCS_PORT"
        break
    fi
    [ "$i" -eq 30 ] && fail "BCS failed to start (see $BCS_LOG)"
    sleep 0.2
done

BCS_WS_URL="ws://127.0.0.1:${BCS_PORT}/ws/bot"

# ── Step 5: Link BCN plugin ───────────────────────────────────────────────

# Prefer openclaw-plugins repo, fallback to monorepo path, then submodules
OPENCLAW_PLUGINS_BCN="/Users/ray/ant/projects/openclaw-plugins/packages/openclaw-channel-bcn"
MONOREPO_BCN="$PROJECT_ROOT/../plugin/packages/openclaw-channel-bcn"
if [ -d "$OPENCLAW_PLUGINS_BCN" ]; then
    BCN_SOURCE="$(cd "$OPENCLAW_PLUGINS_BCN" && pwd)"
elif [ -d "$MONOREPO_BCN" ]; then
    BCN_SOURCE="$(cd "$MONOREPO_BCN" && pwd)"
else
    BCN_SOURCE="$PROJECT_ROOT/submodules/openclaw-channel-bcn"
fi

SYSTEM_EXT="/opt/homebrew/lib/node_modules/openclaw/dist/extensions"
if [ ! -d "$SYSTEM_EXT" ]; then
    fail "OpenClaw extensions directory not found: $SYSTEM_EXT"
fi

# Always re-link to ensure it points to the correct source
if [ -d "$BCN_SOURCE" ]; then
    rm -f "$SYSTEM_EXT/bcs"
    ln -s "$BCN_SOURCE" "$SYSTEM_EXT/bcs"
    pass "BCN plugin linked: $SYSTEM_EXT/bcs -> $BCN_SOURCE"

    # Build BCN plugin so dist/ reflects latest source changes
    info "Building BCN plugin..."
    (cd "$BCN_SOURCE" && npx tshy && npx tshy-after) >&2
    pass "BCN plugin built"
else
    fail "BCN plugin source not found: $BCN_SOURCE"
fi

# ── Step 6: Create OpenClaw profiles ──────────────────────────────────────

setup_profile() {
    local bot_name="$1"
    local profile="$2"
    local port="$3"
    local summary="$4"
    local domains="$5"
    local skills="$6"
    local soul="$7"
    local rules="$8"

    local profile_dir="$HOME/.openclaw-${profile}"
    local workspace_dir="$BOTS_DIR/${bot_name}/workspace"
    local skills_dir="$BOTS_DIR/${bot_name}/skills/bcs-coordination"

    # Clean old profile
    rm -rf "$profile_dir" 2>/dev/null || true
    mkdir -p "$profile_dir" "$workspace_dir" "$skills_dir"

    # Copy SKILL.md
    local skill_template="$PROJECT_ROOT/crates/bcs-cli/SKILL.md"
    if [ -f "$skill_template" ]; then
        sed -e 's|./bcs-cli|bcs-cli|g' -e "s|<你的 Bot ID>|$bot_name|g" "$skill_template" \
            > "$skills_dir/SKILL.md"
    fi

    # Bot personality files
    printf '%s\n' "$soul" > "$workspace_dir/SOUL.md"
    printf '%s\n' "$rules" > "$workspace_dir/RULES.md"
    printf '' > "$workspace_dir/MEMORY.md"

    # Format domains/skills as JSON arrays
    local domains_json=$(echo "$domains" | sed 's/,/","/g' | sed 's/^/"/;s/$/"/')
    local skills_json=$(echo "$skills" | sed 's/,/","/g' | sed 's/^/"/;s/$/"/')

    # openclaw.json
    cat > "$profile_dir/openclaw.json" <<OCEOF
{
  "meta": { "lastTouchedVersion": "2026.3.12" },
  "models": {
    "mode": "merge",
    "providers": {
      "$OPENCLAW_OPENAI_PROVIDER_ID": {
        "baseUrl": "$OPENCLAW_OPENAI_BASE_URL",
        "apiKey": "$OPENCLAW_OPENAI_API_KEY",
        "auth": "api-key",
        "api": "openai-completions",
        "models": [{
          "id": "$OPENCLAW_OPENAI_MODEL_ID",
          "name": "$OPENCLAW_OPENAI_MODEL_ID",
          "api": "openai-completions",
          "reasoning": true,
          "input": ["text"],
          "cost": { "input": 0.0025, "output": 0.01, "cacheRead": 0, "cacheWrite": 0 },
          "contextWindow": 100000,
          "maxTokens": 65536
        }]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": { "primary": "$OPENCLAW_OPENAI_PROVIDER_ID/$OPENCLAW_OPENAI_MODEL_ID" },
      "models": { "$OPENCLAW_OPENAI_PROVIDER_ID/$OPENCLAW_OPENAI_MODEL_ID": { "alias": "$OPENCLAW_OPENAI_MODEL_ID" } },
      "workspace": "$workspace_dir",
      "compaction": { "mode": "safeguard" },
      "maxConcurrent": 4,
      "subagents": { "maxConcurrent": 8 }
    },
    "list": [{ "id": "main" }]
  },
  "tools": { "profile": "coding", "alsoAllow": ["bcs_route", "bcs_assign_task", "bcs_send_task_message", "bcs_task_complete"] },
  "messages": { "ackReactionScope": "group-mentions" },
  "commands": { "native": "auto", "nativeSkills": "auto", "restart": true, "ownerDisplay": "raw" },
  "session": { "dmScope": "per-channel-peer" },
  "hooks": { "internal": { "enabled": false } },
  "channels": {
    "bcs": {
      "enabled": true,
      "bcsUrl": "$BCS_WS_URL",
      "botId": "$bot_name",
      "botName": "$bot_name",
      "capabilities": {
        "summary": "$summary",
        "domains": [$domains_json],
        "skills": [$skills_json],
        "scopes": ["production"]
      },
      "heartbeatIntervalMs": 60000,
      "reconnectIntervalMs": 5000,
      "connectionTimeoutMs": 30000
    }
  },
  "gateway": {
    "port": $port,
    "mode": "local",
    "bind": "loopback",
    "controlUi": { "dangerouslyDisableDeviceAuth": true },
    "auth": { "mode": "token", "token": "test_token_${profile}" },
    "tailscale": { "mode": "off", "resetOnExit": false }
  },
  "plugins": {
    "load": { "paths": ["/opt/homebrew/lib/node_modules/openclaw/dist/extensions/bcs"] },
    "entries": { "bcs": { "enabled": true } }
  }
}
OCEOF

    # Copy provider keys if available
    if [ -f "$HOME/.config/moltis/provider_keys.json" ]; then
        mkdir -p "$profile_dir/config"
        cp "$HOME/.config/moltis/provider_keys.json" "$profile_dir/config/" 2>/dev/null || true
    fi

    info "Profile ready: $profile ($bot_name)"
}

# Coordinator — RULES tell it to use bcs_route tool
setup_profile "Coordinator" "$COORDINATOR_PROFILE" "$COORD_PORT" \
    "协调者，负责协调各专家协作" \
    "coordination,management" \
    "coordination,task-dispatch" \
    "你是协调者 Bot。你的职责是协调各专家协作，将任务路由给合适的专家。你不应自己回答专业问题。" \
    "## 核心规则
1. 你是协调者，负责将任务路由给合适的专家。
2. 数据库相关问题（死锁、SQL、慢查询等）→ 路由给 DBA。
3. 运维相关问题（部署、监控、服务可用性等）→ 路由给 DevOps。
4. 你的回复只需简要说明正在转交，不要自己解答专业问题。"

# DBA
setup_profile "DBA" "$DBA_PROFILE" "$DBA_PORT" \
    "数据库专家，负责故障排查和性能优化" \
    "database,deadlock,performance" \
    "database,deadlock,performance" \
    "你是 DBA Bot。你负责数据库故障排查、死锁分析和性能优化。" \
    "- 从数据库角度给出专业分析
- 关注死锁、锁等待、慢查询、事务冲突"

# DevOps
setup_profile "DevOps" "$DEVOPS_PROFILE" "$DEVOPS_PORT" \
    "运维专家，负责部署和运维" \
    "deployment,ops,monitoring" \
    "deployment,ops,monitoring" \
    "你是 DevOps Bot。你负责部署、运维和监控。" \
    "- 从运维角度提供支持
- 关注服务可用性和部署流程"

# ── Step 7: Start OpenClaw gateways ───────────────────────────────────────

start_gateway() {
    local bot_name="$1"
    local profile="$2"
    local port="$3"
    local log="$LOG_DIR/${bot_name}.log"

    info "Starting OpenClaw ($bot_name) on port $port..."
    NODE_TLS_REJECT_UNAUTHORIZED=0 \
    OPENCLAW_GATEWAY_TOKEN="" \
    openclaw --profile "$profile" gateway run --port "$port" > "$log" 2>&1 &
    local pid=$!

    for i in $(seq 1 30); do
        if curl -sf "http://localhost:$port/health" > /dev/null 2>&1; then
            pass "OpenClaw ($bot_name) started on port $port (PID $pid)"
            echo "$pid"
            return 0
        fi
        sleep 1
    done
    fail "OpenClaw ($bot_name) failed to start (see $log)"
}

COORD_PID=$(start_gateway "Coordinator" "$COORDINATOR_PROFILE" "$COORD_PORT")
DBA_PID=$(start_gateway "DBA" "$DBA_PROFILE" "$DBA_PORT")
DEVOPS_PID=$(start_gateway "DevOps" "$DEVOPS_PROFILE" "$DEVOPS_PORT")

# ── Step 8: Wait for BCN plugin to connect + onboard ──────────────────────

get_bot_token() {
    local profile="$1"
    local session_file="$HOME/.openclaw-${profile}/.bcs/session.json"
    if [ -f "$session_file" ]; then
        python3 -c "import json; print(json.load(open('$session_file'))['token'])" 2>/dev/null || echo ""
    else
        echo ""
    fi
}

info "Waiting for bots to connect to BCS..."
sleep 20

onboard_bot() {
    local profile="$1"
    local name="$2"
    local summary="$3"
    local domains="$4"
    local skills="$5"

    local token=$(get_bot_token "$profile")
    if [ -z "$token" ]; then
        info "Token for $name not found, waiting longer..."
        sleep 10
        token=$(get_bot_token "$profile")
    fi
    if [ -z "$token" ]; then
        local bot_log="$LOG_DIR/${name}.log"
        fail "Cannot find token for $name (profile=$profile). Check logs: $bot_log and $BCS_LOG"
    fi

    MOLTIS_BCS_URL="http://127.0.0.1:${BCS_PORT}" \
    "$BCS_CLI" onboard \
        --token "$token" \
        --name "$name" \
        --summary "$summary" \
        --domains "$domains" \
        --skills "$skills" \
        --scopes "production" >&2 || fail "Failed to onboard $name"
    pass "$name onboarded"
}

onboard_bot "$COORDINATOR_PROFILE" "Coordinator" "协调者" "coordination,management" "coordination,task-dispatch"
onboard_bot "$DBA_PROFILE" "DBA" "数据库专家" "database,deadlock,performance" "database,deadlock,performance"
onboard_bot "$DEVOPS_PROFILE" "DevOps" "运维专家" "deployment,ops,monitoring" "deployment,ops,monitoring"

# ── Step 9: Get bot UUIDs from BCS ────────────────────────────────────────

get_bot_uuid() {
    local name="$1"
    curl -sf "http://127.0.0.1:${BCS_PORT}/bots" | \
        python3 -c "
import sys, json
bots = json.load(sys.stdin)
for b in bots:
    n = b.get('capabilities', {}).get('name') or b.get('name')
    if n == '$name':
        print(b['bot_uuid'])
        break
" 2>/dev/null || echo ""
}

COORD_UUID=$(get_bot_uuid "Coordinator")
DBA_UUID=$(get_bot_uuid "DBA")
DEVOPS_UUID=$(get_bot_uuid "DevOps")

info "Bot UUIDs: Coordinator=$COORD_UUID DBA=$DBA_UUID DevOps=$DEVOPS_UUID"

# ── Step 9.5: Make bots public so create-group skips friendship check ─────
# Newly onboarded bots default to visibility=protected, which forces
# friendship checks during create-group. For local E2E we just flip
# everyone to public.
set_visibility() {
    local bot_uuid="$1"
    local token="$2"
    local name="$3"
    if [ -z "$bot_uuid" ] || [ -z "$token" ]; then
        info "Skipping visibility for $name (missing uuid/token)"
        return
    fi
    local resp_code
    resp_code=$(curl -sS -o /dev/null -w "%{http_code}" \
        -X PUT "http://127.0.0.1:${BCS_PORT}/bots/${bot_uuid}/visibility" \
        -H "Authorization: Bearer ${token}" \
        -H "Content-Type: application/json" \
        -d '{"visibility":"public"}')
    if [ "$resp_code" = "200" ]; then
        pass "$name visibility=public"
    else
        info "$name visibility request returned HTTP $resp_code (continuing)"
    fi
}

set_visibility "$COORD_UUID" "$(get_bot_token "$COORDINATOR_PROFILE")" "Coordinator"
set_visibility "$DBA_UUID"   "$(get_bot_token "$DBA_PROFILE")"        "DBA"
set_visibility "$DEVOPS_UUID" "$(get_bot_token "$DEVOPS_PROFILE")"    "DevOps"

# ── Output variables (stdout only) ────────────────────────────────────────

cat <<EOF
BCS_PORT=$BCS_PORT
BCS_URL=http://127.0.0.1:$BCS_PORT
BCS_WS_URL=$BCS_WS_URL
BCS_PID_FILE=$BCS_PID_FILE
BOTS_DIR=$BOTS_DIR
BCS_LOG=$BCS_LOG
LOG_DIR=$LOG_DIR
COORD_PID=$COORD_PID
DBA_PID=$DBA_PID
DEVOPS_PID=$DEVOPS_PID
COORD_PORT=$COORD_PORT
DBA_PORT=$DBA_PORT
DEVOPS_PORT=$DEVOPS_PORT
COORD_UUID=$COORD_UUID
DBA_UUID=$DBA_UUID
DEVOPS_UUID=$DEVOPS_UUID
COORD_TOKEN=$(get_bot_token "$COORDINATOR_PROFILE")
DBA_TOKEN=$(get_bot_token "$DBA_PROFILE")
DEVOPS_TOKEN=$(get_bot_token "$DEVOPS_PROFILE")
COORDINATOR_PROFILE=$COORDINATOR_PROFILE
DBA_PROFILE=$DBA_PROFILE
DEVOPS_PROFILE=$DEVOPS_PROFILE
SERVICE_API_KEY=$BCS_TEST_SERVICE_API_KEY
SERVICE_GROUP_ID=$BCS_TEST_SERVICE_GROUP_ID
EOF
