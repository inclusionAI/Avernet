#!/bin/bash
# ============================================================================
# Master-Slave Service Group E2E Test - Setup
#
# Starts BCS + 2 OpenClaw gateway instances (Coordinator=master, DBA=slave).
# Outputs KEY=VALUE lines on stdout for the runner to eval.
# All progress messages go to stderr.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

BCS_BIN="$PROJECT_ROOT/target/debug/bcs"
BCS_CLI="$PROJECT_ROOT/target/debug/bcs-cli"

BOTS_DIR="$(mktemp -d)"
LOG_DIR="$BOTS_DIR/logs"
CONFIG_DIR="$BOTS_DIR/configs"
mkdir -p "$LOG_DIR" "$CONFIG_DIR"

BCS_LOG="$LOG_DIR/bcs.log"
BCS_PID_FILE="$BOTS_DIR/bcs.pid"

COORDINATOR_PROFILE="bcs_test_ms_coordinator"
DBA_PROFILE="bcs_test_ms_dba"

# ── Helpers ────────────────────────────────────────────────────────────────

pass() { echo -e "  \033[0;32m✓\033[0m $1" >&2; }
fail() { echo -e "  \033[0;31m✗\033[0m $1" >&2; exit 1; }
info() { echo -e "  \033[0;36m→\033[0m $1" >&2; }

# ── Step 1: Build BCS ─────────────────────────────────────────────────────

info "Building BCS + bcs-cli..."
cargo build --package bcs --package bcs-cli --manifest-path "$PROJECT_ROOT/Cargo.toml" 2>&1 | tail -1 >&2
pass "BCS built"

# ── Step 2: Fixed ports ───────────────────────────────────────────────────

BCS_PORT="${BCS_PORT:-21000}"
COORD_PORT="${COORD_PORT:-21200}"
DBA_PORT="${DBA_PORT:-21300}"
OPENCLAW_OPENAI_PROVIDER_ID="${OPENCLAW_OPENAI_PROVIDER_ID:-openai_compatible}"
OPENCLAW_OPENAI_BASE_URL="${OPENCLAW_OPENAI_BASE_URL:-https://api.openai.com/v1}"
OPENCLAW_OPENAI_API_KEY="${OPENCLAW_OPENAI_API_KEY:-}"
OPENCLAW_OPENAI_MODEL_ID="${OPENCLAW_OPENAI_MODEL_ID:-gpt-4.1-mini}"

# ── Step 3: Write BCS config ─────────────────────────────────────────────

cat > "$CONFIG_DIR/bcs-config.toml" <<EOF
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

[logging]
default_level = "info"
console = true

[[logging.outputs]]
name = "main"
path = "${LOG_DIR}"
file = "bcs-main.log"
level = "info"
rotation = "daily"
targets = []
max_keep_days = 1
EOF

# ── Step 4: Start BCS ─────────────────────────────────────────────────────

info "Starting BCS on port $BCS_PORT..."
SERVER_ENV="${SERVER_ENV:-local}" BCS_CONFIG_DIR="$CONFIG_DIR" BCS_DATA_DIR="$BOTS_DIR" RUST_LOG=info "$BCS_BIN" > "$BCS_LOG" 2>&1 &
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

OPENCLAW_PLUGINS_BCN="${OPENCLAW_PLUGINS_BCN:-}"
MONOREPO_BCN="$PROJECT_ROOT/crates/plugins/openclaw-channel-bcn"
if [ -d "$MONOREPO_BCN" ]; then
    BCN_SOURCE="$(cd "$MONOREPO_BCN" && pwd)"
elif [ -n "$OPENCLAW_PLUGINS_BCN" ] && [ -d "$OPENCLAW_PLUGINS_BCN" ]; then
    BCN_SOURCE="$(cd "$OPENCLAW_PLUGINS_BCN" && pwd)"
else
    BCN_SOURCE="$PROJECT_ROOT/submodules/openclaw-channel-bcn"
fi

SYSTEM_EXT="/opt/homebrew/lib/node_modules/openclaw/dist/extensions"
if [ ! -d "$SYSTEM_EXT" ]; then
    fail "OpenClaw extensions directory not found: $SYSTEM_EXT"
fi

if [ -d "$BCN_SOURCE" ]; then
    rm -f "$SYSTEM_EXT/bcs"
    ln -s "$BCN_SOURCE" "$SYSTEM_EXT/bcs"
    pass "BCN plugin linked: $SYSTEM_EXT/bcs -> $BCN_SOURCE"

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

    rm -rf "$profile_dir" 2>/dev/null || true
    mkdir -p "$profile_dir" "$workspace_dir"

    printf '%s\n' "$soul" > "$workspace_dir/SOUL.md"
    printf '%s\n' "$rules" > "$workspace_dir/RULES.md"
    printf '' > "$workspace_dir/MEMORY.md"

    local domains_json=$(echo "$domains" | sed 's/,/","/g' | sed 's/^/"/;s/$/"/')
    local skills_json=$(echo "$skills" | sed 's/,/","/g' | sed 's/^/"/;s/$/"/')

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
          "cost": { "input": 0.002, "output": 0.008, "cacheRead": 0, "cacheWrite": 0 },
          "contextWindow": 131072,
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

    if [ -f "$HOME/.config/moltis/provider_keys.json" ]; then
        mkdir -p "$profile_dir/config"
        cp "$HOME/.config/moltis/provider_keys.json" "$profile_dir/config/" 2>/dev/null || true
    fi

    info "Profile ready: $profile ($bot_name)"
}

# Coordinator (master)
setup_profile "Coordinator" "$COORDINATOR_PROFILE" "$COORD_PORT" \
    "协调者，负责协调各专家协作" \
    "coordination,management" \
    "coordination,task-dispatch" \
    "你是 master bot (Coordinator)。你的职责是使用 bcs_assign_task 工具将任务分配给子 bot，收到回复后综合分析，最后调用 bcs_task_complete 提交总结。" \
    "## 核心规则
1. 收到任务后，使用 bcs_assign_task 分配给合适的子 bot。
2. 收到子 bot 回复后，综合分析并给出结论。
3. 任务完成后，调用 bcs_task_complete 提交最终总结。
4. 不要自己回答专业问题，交给子 bot 处理。"

# DBA (slave)
setup_profile "DBA" "$DBA_PROFILE" "$DBA_PORT" \
    "数据库专家，负责故障排查和性能优化" \
    "database,deadlock,performance" \
    "database,deadlock,performance" \
    "你是 slave bot (DBA)。你负责数据库故障排查、死锁分析和性能优化。收到 master 分配的任务后直接处理并回复。" \
    "- 从数据库角度给出专业分析
- 关注死锁、锁等待、慢查询、事务冲突
- 收到任务后直接回复分析结果，不要主动发起对话"

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

info "Bot UUIDs: Coordinator=$COORD_UUID DBA=$DBA_UUID"
info "Gateway tokens: Coordinator=test_token_${COORDINATOR_PROFILE} DBA=test_token_${DBA_PROFILE}"
info "Gateway URLs: Coordinator=http://localhost:${COORD_PORT} DBA=http://localhost:${DBA_PORT}"

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
COORD_PORT=$COORD_PORT
DBA_PORT=$DBA_PORT
COORD_UUID=$COORD_UUID
DBA_UUID=$DBA_UUID
COORD_TOKEN=$(get_bot_token "$COORDINATOR_PROFILE")
DBA_TOKEN=$(get_bot_token "$DBA_PROFILE")
COORDINATOR_PROFILE=$COORDINATOR_PROFILE
DBA_PROFILE=$DBA_PROFILE
COORD_GATEWAY_TOKEN=test_token_${COORDINATOR_PROFILE}
DBA_GATEWAY_TOKEN=test_token_${DBA_PROFILE}
EOF
