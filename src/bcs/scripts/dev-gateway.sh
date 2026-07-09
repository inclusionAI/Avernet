#!/bin/bash
# ============================================================================
# BCS + OpenClaw Integration Dev Script
#
# Builds BCS, links the current BCN plugin, starts BCS + 3 OpenClaw
# instances (Coordinator, DBA, DevOps), and completes onboard.
#
# Usage:
#   bash scripts/dev-gateway.sh
#
# Prerequisites:
#   - openclaw CLI installed (/opt/homebrew/bin/openclaw)
#   - Rust toolchain (cargo)
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLUGIN_SOURCE="${PLUGIN_SOURCE:-$PROJECT_ROOT/crates/plugins/openclaw-channel-bcn}"
OPENCLAW_EXT="/opt/homebrew/lib/node_modules/openclaw/dist/extensions"

BCS_BIN="$PROJECT_ROOT/target/debug/bcs"
BCS_CLI="$PROJECT_ROOT/target/debug/bcs-cli"

# Ports
BCS_PORT="${BCS_PORT:-21000}"
COORD_PORT="${COORD_PORT:-21200}"
DBA_PORT="${DBA_PORT:-21300}"
DEVOPS_PORT="${DEVOPS_PORT:-21400}"

# Model provider credentials
OPENCLAW_OPENAI_BASE_URL="${OPENCLAW_OPENAI_BASE_URL:?Set OPENCLAW_OPENAI_BASE_URL to the model API base URL}"
OPENCLAW_OPENAI_API_KEY="${OPENCLAW_OPENAI_API_KEY:?Set OPENCLAW_OPENAI_API_KEY to the model API key}"
OPENCLAW_OPENAI_MODEL_ID="${OPENCLAW_OPENAI_MODEL_ID:?Set OPENCLAW_OPENAI_MODEL_ID to the model id}"
OPENCLAW_OPENAI_PROVIDER_ID="${OPENCLAW_OPENAI_PROVIDER_ID:-openai_compatible}"

# Profile names
COORD_PROFILE="bcs_dev_coordinator"
DBA_PROFILE="bcs_dev_dba"
DEVOPS_PROFILE="bcs_dev_devops"

# Temp dir
WORK_DIR="$(mktemp -d)"
LOG_DIR="$WORK_DIR/logs"
CONFIG_DIR="$WORK_DIR/configs"
mkdir -p "$LOG_DIR" "$CONFIG_DIR"

BCS_LOG="$LOG_DIR/bcs.log"
BCS_PID_FILE="$WORK_DIR/bcs.pid"

COORD_PID=""
DBA_PID=""
DEVOPS_PID=""

# ── Helpers ────────────────────────────────────────────────────────────────

pass() { echo -e "  \033[0;32m✓\033[0m $1"; }
fail() { echo -e "  \033[0;31m✗\033[0m $1" >&2; exit 1; }
info() { echo -e "  \033[0;36m→\033[0m $1"; }

# ── Teardown ───────────────────────────────────────────────────────────────

cleanup() {
    echo ""
    echo "-- Teardown --"

    if [ -f "$BCS_PID_FILE" ]; then
        local bcs_pid=$(cat "$BCS_PID_FILE")
        if kill -0 "$bcs_pid" 2>/dev/null; then
            kill "$bcs_pid" 2>/dev/null || true
            wait "$bcs_pid" 2>/dev/null || true
            pass "BCS stopped"
        fi
        rm -f "$BCS_PID_FILE"
    fi

    for pid_var in COORD_PID DBA_PID DEVOPS_PID; do
        local pid="${!pid_var:-}"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            sleep 0.5
            kill -9 "$pid" 2>/dev/null || true
            pass "OpenClaw stopped (pid=$pid)"
        fi
    done

    # Save logs
    local last_logs="$SCRIPT_DIR/.dev-gateway-logs"
    if [ -d "$LOG_DIR" ]; then
        rm -rf "$last_logs"
        cp -r "$LOG_DIR" "$last_logs"
        pass "Logs saved to $last_logs"
    fi

    for profile in "$COORD_PROFILE" "$DBA_PROFILE" "$DEVOPS_PROFILE"; do
        rm -rf "$HOME/.openclaw-${profile}" 2>/dev/null || true
    done

    rm -rf "$WORK_DIR" 2>/dev/null || true
    pass "Cleaned up"
}
trap cleanup EXIT

echo ""
echo "== BCS + OpenClaw Dev Environment (3 bots) =="
echo ""

# ── Step 1: Build BCS ─────────────────────────────────────────────────────

info "Building BCS + bcs-cli..."
cargo build --package bcs --package bcs-cli --manifest-path "$PROJECT_ROOT/Cargo.toml" 2>&1 | tail -1
pass "BCS built"

# ── Step 2: Write BCS config ─────────────────────────────────────────────

BCS_WS_URL="ws://127.0.0.1:${BCS_PORT}/ws/bot"

cat > "$CONFIG_DIR/bcs-config.toml" <<EOF
bind = "127.0.0.1"
port = ${BCS_PORT}
bots_base_dir = "${WORK_DIR}"
store_messages = true
max_groups_as_driver = 10
max_group_members = 10
max_groups_as_member = 20
max_group_messages = 100
group_chat_delay_min_ms = 0
group_chat_delay_max_ms = 0
strict_container_validation = false
EOF

# ── Step 3: Start BCS ────────────────────────────────────────────────────

info "Starting BCS on port $BCS_PORT..."
SERVER_ENV="${SERVER_ENV:-local}" BCS_DATA_DIR="$WORK_DIR" RUST_LOG=info "$BCS_BIN" > "$BCS_LOG" 2>&1 &
echo $! > "$BCS_PID_FILE"

for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:${BCS_PORT}/health" > /dev/null 2>&1; then
        pass "BCS healthy on port $BCS_PORT"
        break
    fi
    [ "$i" -eq 30 ] && fail "BCS failed to start (see $BCS_LOG)"
    sleep 0.2
done

# ── Step 4: Link BCN plugin ──────────────────────────────────────────────

if [ ! -d "$PLUGIN_SOURCE" ]; then
    fail "BCN plugin source not found: $PLUGIN_SOURCE"
fi

if [ ! -d "$OPENCLAW_EXT" ]; then
    fail "OpenClaw extensions directory not found: $OPENCLAW_EXT"
fi

info "Building BCN plugin..."
(cd "$PLUGIN_SOURCE" && npx tshy && npx tshy-after) 2>&1 | tail -3
pass "BCN plugin built"

rm -f "$OPENCLAW_EXT/openclaw-channel-bcn"
ln -s "$PLUGIN_SOURCE" "$OPENCLAW_EXT/openclaw-channel-bcn"
pass "BCN plugin linked: $OPENCLAW_EXT/openclaw-channel-bcn -> $PLUGIN_SOURCE"

# ── Step 5: Create OpenClaw profiles ─────────────────────────────────────

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
    local workspace_dir="$WORK_DIR/${bot_name}/workspace"
    local skills_dir="$WORK_DIR/${bot_name}/skills/bcs-coordination"

    rm -rf "$profile_dir" 2>/dev/null || true
    mkdir -p "$profile_dir" "$workspace_dir" "$skills_dir"

    local skill_template="$PROJECT_ROOT/crates/bcs-cli/SKILL.md"
    if [ -f "$skill_template" ]; then
        sed -e 's|./bcs-cli|bcs-cli|g' -e "s|<你的 Bot ID>|$bot_name|g" "$skill_template" \
            > "$skills_dir/SKILL.md"
    fi

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
  "tools": { "profile": "coding", "alsoAllow": ["bcs_route"] },
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
    "auth": { "mode": "token", "token": "dev_token_${profile}" },
    "tailscale": { "mode": "off", "resetOnExit": false }
  },
  "plugins": {
    "load": { "paths": ["$OPENCLAW_EXT/openclaw-channel-bcn"] },
    "entries": { "openclaw-channel-bcn": { "enabled": true } }
  }
}
OCEOF

    if [ -f "$HOME/.config/moltis/provider_keys.json" ]; then
        mkdir -p "$profile_dir/config"
        cp "$HOME/.config/moltis/provider_keys.json" "$profile_dir/config/" 2>/dev/null || true
    fi

    info "Profile ready: $profile ($bot_name)"
}

setup_profile "Coordinator" "$COORD_PROFILE" "$COORD_PORT" \
    "协调者，负责协调各专家协作" \
    "coordination,management" \
    "coordination,task-dispatch" \
    "你是协调者 Bot。你的职责是协调各专家协作，将任务路由给合适的专家。你不应自己回答专业问题。" \
    "## 核心规则
1. 你是协调者，负责将任务路由给合适的专家。
2. 数据库相关问题（死锁、SQL、慢查询等）→ 路由给 DBA。
3. 运维相关问题（部署、监控、服务可用性等）→ 路由给 DevOps。
4. 你的回复只需简要说明正在转交，不要自己解答专业问题。"

setup_profile "DBA" "$DBA_PROFILE" "$DBA_PORT" \
    "数据库专家，负责故障排查和性能优化" \
    "database,deadlock,performance" \
    "database,deadlock,performance" \
    "你是 DBA Bot。你负责数据库故障排查、死锁分析和性能优化。" \
    "- 从数据库角度给出专业分析
- 关注死锁、锁等待、慢查询、事务冲突"

setup_profile "DevOps" "$DEVOPS_PROFILE" "$DEVOPS_PORT" \
    "运维专家，负责部署和运维" \
    "deployment,ops,monitoring" \
    "deployment,ops,monitoring" \
    "你是 DevOps Bot。你负责部署、运维和监控。" \
    "- 从运维角度提供支持
- 关注服务可用性和部署流程"

# ── Step 6: Start OpenClaw gateways ──────────────────────────────────────

start_gateway() {
    local bot_name="$1"
    local profile="$2"
    local port="$3"
    local log="$LOG_DIR/${bot_name}.log"

    info "Starting OpenClaw ($bot_name) on port $port..."
    NODE_TLS_REJECT_UNAUTHORIZED=0 \
    OPENCLAW_GATEWAY_TOKEN="" \
    MAC_CONTAINER=true \
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

COORD_PID=$(start_gateway "Coordinator" "$COORD_PROFILE" "$COORD_PORT")
DBA_PID=$(start_gateway "DBA" "$DBA_PROFILE" "$DBA_PORT")
DEVOPS_PID=$(start_gateway "DevOps" "$DEVOPS_PROFILE" "$DEVOPS_PORT")

# ── Step 7: Wait for BCN plugin to connect + onboard ────────────────────

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
sleep 8

onboard_bot() {
    local profile="$1"
    local name="$2"
    local summary="$3"
    local domains="$4"
    local skills="$5"

    local token=$(get_bot_token "$profile")
    if [ -z "$token" ]; then
        info "Token for $name not found, waiting longer..."
        sleep 8
        token=$(get_bot_token "$profile")
    fi
    if [ -z "$token" ]; then
        info "Token for $name still not found, last attempt..."
        sleep 10
        token=$(get_bot_token "$profile")
    fi
    if [ -z "$token" ]; then
        fail "Cannot find token for $name (profile=$profile)"
    fi

    MOLTIS_BCS_URL="http://127.0.0.1:${BCS_PORT}" \
    "$BCS_CLI" onboard \
        --token "$token" \
        --name "$name" \
        --summary "$summary" \
        --domains "$domains" \
        --skills "$skills" \
        --scopes "production" || fail "Failed to onboard $name"
    pass "$name onboarded"
}

onboard_bot "$COORD_PROFILE" "Coordinator" "协调者" "coordination,management" "coordination,task-dispatch"
onboard_bot "$DBA_PROFILE" "DBA" "数据库专家" "database,deadlock,performance" "database,deadlock,performance"
onboard_bot "$DEVOPS_PROFILE" "DevOps" "运维专家" "deployment,ops,monitoring" "deployment,ops,monitoring"

# ── Step 8: Get bot UUIDs ────────────────────────────────────────────────

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
" 2>/dev/null || echo "unknown"
}

COORD_UUID=$(get_bot_uuid "Coordinator")
DBA_UUID=$(get_bot_uuid "DBA")
DEVOPS_UUID=$(get_bot_uuid "DevOps")

# ── Ready ────────────────────────────────────────────────────────────────

echo ""
echo "== Dev Environment Ready =="
echo ""
echo "  BCS:         http://127.0.0.1:${BCS_PORT}"
echo "  Coordinator: http://localhost:${COORD_PORT}  (${COORD_UUID})"
echo "  DBA:         http://localhost:${DBA_PORT}  (${DBA_UUID})"
echo "  DevOps:      http://localhost:${DEVOPS_PORT}  (${DEVOPS_UUID})"
echo "  Logs:        $LOG_DIR"
echo ""
echo "  Plugin:      $PLUGIN_SOURCE -> $OPENCLAW_EXT/openclaw-channel-bcn"
echo ""
echo "Press Enter to teardown and exit..."
read -r _
