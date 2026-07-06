#!/bin/bash
# Start Three OpenClaw Bots: reviewer, legal, and database demo bots
# 使用 OpenClaw 的 BCN plugin 连接到 BCS

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOTS_BASE_DIR="$PROJECT_ROOT/three_openclaw_test_dir"
BCS_PORT="${BCS_PORT:-21000}"
#BCS_URL="wss://bcs-pre.example.com/ws/bot"
BCS_URL="ws://127.0.0.1:21000/ws/bot"
BCS_CLI="$PROJECT_ROOT/target/debug/bcs-cli"
OPENCLAW_OPENAI_PROVIDER_ID="${OPENCLAW_OPENAI_PROVIDER_ID:-openai_compatible}"
OPENCLAW_OPENAI_BASE_URL="${OPENCLAW_OPENAI_BASE_URL:-https://api.openai.com/v1}"
OPENCLAW_OPENAI_API_KEY="${OPENCLAW_OPENAI_API_KEY:-}"
OPENCLAW_OPENAI_MODEL_ID="${OPENCLAW_OPENAI_MODEL_ID:-gpt-4.1-mini}"

LOG_DIR="$BOTS_BASE_DIR/logs"

# ============================================================================
# Bot Configurations
# ============================================================================

# Bot 1: Reviewer demo bot
BOT1_ID="Reviewer Bot"
BOT1_PROFILE="bcs_shenli"
BOT1_PORT=30091
BOT1_SUMMARY="审核专家，负责文档、合同和规则符合性审核"
BOT1_DOMAINS="review,compliance,audit"
BOT1_SKILLS="review,compliance,audit"
BOT1_SCOPES="production"

# Bot 2: Legal demo bot
BOT2_ID="Legal Bot"
BOT2_PROFILE="bcs_fawu"
BOT2_PORT=30101
BOT2_SUMMARY="法务顾问，负责合规、条款风险和法律审查"
BOT2_DOMAINS="legal,compliance,contract"
BOT2_SKILLS="legal,compliance,contract"
BOT2_SCOPES="production"

# Bot 3: Database demo bot
BOT3_ID="Database Bot"
BOT3_PROFILE="bcs_dba"
BOT3_PORT=30111
BOT3_SUMMARY="数据库专家，负责数据库故障排查和性能优化"
BOT3_DOMAINS="database,deadlock,performance"
BOT3_SKILLS="database,deadlock,performance"
BOT3_SCOPES="production"

# Fixed gateway tokens for each bot
BOT1_GATEWAY_TOKEN="bcs_shenli_token_2024"
BOT2_GATEWAY_TOKEN="bcs_fawu_token_2024"
BOT3_GATEWAY_TOKEN="bcs_dba_token_2024"

# ============================================================================
# DingTalk Configuration
# ============================================================================

# Bot 1
BOT1_DINGTALK_CLIENT_ID="${BOT1_DINGTALK_CLIENT_ID:-}"
BOT1_DINGTALK_CLIENT_SECRET="${BOT1_DINGTALK_CLIENT_SECRET:-}"
BOT1_DINGTALK_ROBOT_CODE="${BOT1_DINGTALK_ROBOT_CODE:-}"
BOT1_DINGTALK_CORP_ID="${BOT1_DINGTALK_CORP_ID:-}"
BOT1_DINGTALK_AGENT_ID="${BOT1_DINGTALK_AGENT_ID:-}"

# Bot 2
BOT2_DINGTALK_CLIENT_ID="${BOT2_DINGTALK_CLIENT_ID:-}"
BOT2_DINGTALK_CLIENT_SECRET="${BOT2_DINGTALK_CLIENT_SECRET:-}"
BOT2_DINGTALK_ROBOT_CODE="${BOT2_DINGTALK_ROBOT_CODE:-}"
BOT2_DINGTALK_CORP_ID="${BOT2_DINGTALK_CORP_ID:-}"
BOT2_DINGTALK_AGENT_ID="${BOT2_DINGTALK_AGENT_ID:-}"

# Bot 3
BOT3_DINGTALK_CLIENT_ID="${BOT3_DINGTALK_CLIENT_ID:-}"
BOT3_DINGTALK_CLIENT_SECRET="${BOT3_DINGTALK_CLIENT_SECRET:-}"
BOT3_DINGTALK_ROBOT_CODE="${BOT3_DINGTALK_ROBOT_CODE:-}"
BOT3_DINGTALK_CORP_ID="${BOT3_DINGTALK_CORP_ID:-}"
BOT3_DINGTALK_AGENT_ID="${BOT3_DINGTALK_AGENT_ID:-}"

BOT1_PID=""
BOT2_PID=""
BOT3_PID=""

# ============================================================================
# Colors
# ============================================================================

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "  ${CYAN}→${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }

# ============================================================================
# Cleanup
# ============================================================================

cleanup() {
    echo ""
    info "Cleaning up..."
    [ -n "$BOT1_PID" ] && kill "$BOT1_PID" 2>/dev/null || true
    [ -n "$BOT2_PID" ] && kill "$BOT2_PID" 2>/dev/null || true
    [ -n "$BOT3_PID" ] && kill "$BOT3_PID" 2>/dev/null || true
    sleep 1
    [ -n "$BOT1_PID" ] && kill -9 "$BOT1_PID" 2>/dev/null || true
    [ -n "$BOT2_PID" ] && kill -9 "$BOT2_PID" 2>/dev/null || true
    [ -n "$BOT3_PID" ] && kill -9 "$BOT3_PID" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

# ============================================================================
# Setup OpenClaw Profile Directory
# ============================================================================

setup_profile_dir() {
    local bot_id="$1"
    local profile="$2"
    local port="$3"
    local summary="$4"
    local domains="$5"
    local skills="$6"
    local scopes="$7"
    local soul="$8"
    local rules="$9"
    local memory="${10}"
    local gateway_token="${11}"
    local dingtalk_client_id="${12}"
    local dingtalk_client_secret="${13}"
    local dingtalk_robot_code="${14}"
    local dingtalk_corp_id="${15}"
    local dingtalk_agent_id="${16}"

    local profile_dir="$HOME/.openclaw-${profile}"
    local workspace_dir="$BOTS_BASE_DIR/$bot_id/workspace"
    local skills_dir="$workspace_dir/../skills"
    local dingtalk_ext_path="$HOME/.openclaw/extensions/dingtalk"

    mkdir -p "$profile_dir" "$workspace_dir" "$skills_dir" "$LOG_DIR"

    # Copy the entire bcs-coordination skill directory (SKILL.md + references/)
    local skill_source_dir="$PROJECT_ROOT/crates/bcs-cli/bcs-coordination"
    if [ -d "$skill_source_dir" ]; then
        cp -r "$skill_source_dir" "$skills_dir/"
        # Patch SKILL.md with bot-specific values
        if [ -f "$skills_dir/bcs-coordination/SKILL.md" ]; then
            sed -i '' -e 's|./bcs-cli|bcs-cli|g' -e "s|<你的 Bot ID>|$bot_id|g" "$skills_dir/bcs-coordination/SKILL.md"
        fi
    fi

    # Check if dingtalk extension exists
    local dingtalk_enabled="true"
    if [ ! -d "$dingtalk_ext_path" ]; then
        warn "DingTalk extension not found at $dingtalk_ext_path"
        info "DingTalk channel will be disabled for $bot_id"
        dingtalk_enabled="false"
    fi

    # Create SOUL, RULES, MEMORY files in workspace
    printf '%s\n' "$soul" > "$workspace_dir/SOUL.md"
    printf '%s\n' "$rules" > "$workspace_dir/RULES.md"
    printf '%s\n' "$memory" > "$workspace_dir/MEMORY.md"

    # Create openclaw.json config
    cat > "$profile_dir/openclaw.json" << EOF
{
  "meta": {
    "lastTouchedVersion": "2026.3.12"
  },
  "models": {
    "mode": "merge",
    "providers": {
      "$OPENCLAW_OPENAI_PROVIDER_ID": {
        "baseUrl": "$OPENCLAW_OPENAI_BASE_URL",
        "apiKey": "$OPENCLAW_OPENAI_API_KEY",
        "auth": "api-key",
        "api": "openai-completions",
        "models": [
          {
            "id": "$OPENCLAW_OPENAI_MODEL_ID",
            "name": "$OPENCLAW_OPENAI_MODEL_ID",
            "api": "openai-completions",
            "reasoning": true,
            "input": ["text"],
            "cost": {
              "input": 0.0025,
              "output": 0.01,
              "cacheRead": 0,
              "cacheWrite": 0
            },
            "contextWindow": 100000,
            "maxTokens": 65536
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "$OPENCLAW_OPENAI_PROVIDER_ID/$OPENCLAW_OPENAI_MODEL_ID"
      },
      "models": {
        "$OPENCLAW_OPENAI_PROVIDER_ID/$OPENCLAW_OPENAI_MODEL_ID": {
          "alias": "$OPENCLAW_OPENAI_MODEL_ID"
        }
      },
      "workspace": "$workspace_dir",
      "compaction": {
        "mode": "safeguard"
      },
      "maxConcurrent": 4,
      "subagents": {
        "maxConcurrent": 8
      }
    },
    "list": [
      {
        "id": "main"
      }
    ]
  },
  "tools": {
    "profile": "coding"
  },
  "messages": {
    "ackReactionScope": "group-mentions"
  },
  "commands": {
    "native": "auto",
    "nativeSkills": "auto",
    "restart": true,
    "ownerDisplay": "raw"
  },
  "session": {
    "dmScope": "per-channel-peer"
  },
  "hooks": {
    "internal": {
      "enabled": true,
      "entries": {
        "boot-md": {
          "enabled": true
        }
      }
    }
  },
  "channels": {
    "bcs": {
      "enabled": true,
      "bcsUrl": "$BCS_URL",
      "botId": "$bot_id",
      "botName": "$bot_id",
      "capabilities": {
        "summary": "$summary",
        "domains": [$(echo "$domains" | sed 's/,/","/g' | sed 's/^/"/;s/$/"/')],
        "skills": [$(echo "$skills" | sed 's/,/","/g' | sed 's/^/"/;s/$/"/')],
        "scopes": [$(echo "$scopes" | sed 's/,/","/g' | sed 's/^/"/;s/$/"/')]
      },
      "heartbeatIntervalMs": 60000,
      "reconnectIntervalMs": 5000,
      "connectionTimeoutMs": 30000
    }$(if [ "$dingtalk_enabled" = "true" ]; then cat <<DINGTALK_CONFIG
,
    "dingtalk": {
      "enabled": true,
      "clientId": "$dingtalk_client_id",
      "clientSecret": "$dingtalk_client_secret",
      "robotCode": "$dingtalk_robot_code",
      "corpId": "$dingtalk_corp_id",
      "agentId": "$dingtalk_agent_id",
      "enableAICard": false,
      "dmPolicy": "open",
      "groupPolicy": "open",
      "messageType": "markdown",
      "allowFrom": ["*"]
    }
DINGTALK_CONFIG
fi)
  },
  "gateway": {
    "port": $port,
    "mode": "local",
    "bind": "loopback",
    "controlUi": {
      "dangerouslyDisableDeviceAuth": true
    },
    "auth": {
      "mode": "token",
      "token": "$gateway_token"
    },
    "tailscale": {
      "mode": "off",
      "resetOnExit": false
    },
    "nodes": {
      "denyCommands": [
        "camera.snap",
        "camera.clip",
        "screen.record",
        "calendar.add",
        "contacts.add",
        "reminders.add"
      ]
    }
  },
  "plugins": {
    "load": {
      "paths": [
        "/opt/homebrew/lib/node_modules/openclaw/extensions/openclaw-channel-bcn"$(if [ "$dingtalk_enabled" = "true" ]; then echo ","; echo "        \"$dingtalk_ext_path\""; fi)
      ]
    },
    "entries": {
      "openclaw-channel-bcn": {
        "enabled": true
      }$(if [ "$dingtalk_enabled" = "true" ]; then cat <<DINGTALK_PLUGIN
,
      "dingtalk": {
        "enabled": true
      }
DINGTALK_PLUGIN
fi)
    }
  }
}
EOF

    # Copy provider keys if exists
    if [ -f "$HOME/.config/moltis/provider_keys.json" ]; then
        mkdir -p "$profile_dir/config"
        cp "$HOME/.config/moltis/provider_keys.json" "$profile_dir/config/" 2>/dev/null || true
    fi

    info "Profile directory setup complete: $profile ($bot_id)"
}

# ============================================================================
# Start OpenClaw Gateway
# ============================================================================

start_openclaw() {
    local bot_id="$1"
    local profile="$2"
    local port="$3"
    local log_file="$4"

    info "Starting OpenClaw ($bot_id) on port $port with profile $profile..."

    # Run OpenClaw gateway in background
    # NODE_TLS_REJECT_UNAUTHORIZED=0 disables TLS certificate verification (for debugging)
    NODE_TLS_REJECT_UNAUTHORIZED=0 \
    OPENCLAW_GATEWAY_TOKEN="" \
    openclaw --profile "$profile" gateway run --port "$port" &> "$log_file" &
    local pid=$!

    # Wait for gateway to be ready
    for i in $(seq 1 30); do
        if curl -s "http://localhost:$port/health" > /dev/null 2>&1; then
            pass "OpenClaw ($bot_id) started on port $port (PID $pid)"
            echo "$pid"
            return 0
        fi
        sleep 1
    done

    fail "OpenClaw ($bot_id) failed to start (check $log_file)"
    echo ""
    return 1
}

# ============================================================================
# Stop All Bots
# ============================================================================

stop_all() {
    local force="${1:-false}"

    info "Stopping all OpenClaw bots..."

    # Kill by port
    for port in $BOT1_PORT $BOT2_PORT $BOT3_PORT; do
        local pids=$(lsof -ti :$port 2>/dev/null || true)
        if [ -n "$pids" ]; then
            info "Stopping processes on port $port: $pids"
            echo "$pids" | xargs kill 2>/dev/null || true
            sleep 1
            echo "$pids" | xargs kill -9 2>/dev/null || true
        fi
    done

    # Force stop all openclaw gateway processes if requested
    if [ "$force" = "true" ]; then
        info "Force stopping all openclaw gateway processes..."
        pkill -f "openclaw.*gateway" 2>/dev/null || true
        sleep 1
        pkill -9 -f "openclaw.*gateway" 2>/dev/null || true
    fi

    pass "All bots stopped"
}

# ============================================================================
# Show Status
# ============================================================================

show_status() {
    echo ""
    info "Bot Status:"

    for bot_info in "$BOT1_ID:$BOT1_PORT:$BOT1_PROFILE" "$BOT2_ID:$BOT2_PORT:$BOT2_PROFILE" "$BOT3_ID:$BOT3_PORT:$BOT3_PROFILE"; do
        local bot_id="${bot_info%%:*}"
        local rest="${bot_info#*:}"
        local port="${rest%%:*}"
        local profile="${rest#*:}"
        if curl -s "http://localhost:$port/health" > /dev/null 2>&1; then
            pass "$bot_id (port $port, profile $profile): running"
        else
            warn "$bot_id (port $port, profile $profile): not running"
        fi
    done
    echo ""
}

# ============================================================================
# Get Bot Token
# ============================================================================

get_bot_token() {
    local profile="$1"
    local profile_dir="$HOME/.openclaw-${profile}"
    local session_file="$profile_dir/.bcs/session.json"
    if [ -f "$session_file" ]; then
        local token=$(cat "$session_file" | grep -o '"token"[[:space:]]*:[[:space:]]*"[^"]*"' | grep -o '"[^"]*"$' | tr -d '"')
        echo "$token"
    else
        echo ""
    fi
}

# ============================================================================
# Bot Onboarding
# ============================================================================

cmd_onboard() {
    info "Completing bot onboarding..."

    # Wait for bots to connect and get tokens
    info "Waiting for bots to connect to BCS..."
    sleep 5

    # Onboard Bot 1
    local token1=$(get_bot_token "$BOT1_PROFILE")
    if [ -z "$token1" ]; then
        warn "Token for $BOT1_ID not found, waiting longer..."
        sleep 5
        token1=$(get_bot_token "$BOT1_PROFILE")
    fi

    if [ -n "$token1" ]; then
        info "Onboarding $BOT1_ID..."
        "$BCS_CLI" onboard \
            --token "$token1" \
            --name "$BOT1_ID" \
            --summary "$BOT1_SUMMARY" \
            --domains "$BOT1_DOMAINS" \
            --skills "$BOT1_SKILLS" \
            --scopes "$BOT1_SCOPES" || {
            fail "Failed to onboard $BOT1_ID"
            return 1
        }
        pass "$BOT1_ID onboarded successfully!"
    else
        fail "Cannot find token for $BOT1_ID. Make sure bot is connected to BCS."
        return 1
    fi

    # Onboard Bot 2
    local token2=$(get_bot_token "$BOT2_PROFILE")
    if [ -z "$token2" ]; then
        sleep 5
        token2=$(get_bot_token "$BOT2_PROFILE")
    fi

    if [ -n "$token2" ]; then
        info "Onboarding $BOT2_ID..."
        "$BCS_CLI" onboard \
            --token "$token2" \
            --name "$BOT2_ID" \
            --summary "$BOT2_SUMMARY" \
            --domains "$BOT2_DOMAINS" \
            --skills "$BOT2_SKILLS" \
            --scopes "$BOT2_SCOPES" || {
            fail "Failed to onboard $BOT2_ID"
            return 1
        }
        pass "$BOT2_ID onboarded successfully!"
    else
        fail "Cannot find token for $BOT2_ID. Make sure bot is connected to BCS."
        return 1
    fi

    # Onboard Bot 3
    local token3=$(get_bot_token "$BOT3_PROFILE")
    if [ -z "$token3" ]; then
        sleep 5
        token3=$(get_bot_token "$BOT3_PROFILE")
    fi

    if [ -n "$token3" ]; then
        info "Onboarding $BOT3_ID..."
        "$BCS_CLI" onboard \
            --token "$token3" \
            --name "$BOT3_ID" \
            --summary "$BOT3_SUMMARY" \
            --domains "$BOT3_DOMAINS" \
            --skills "$BOT3_SKILLS" \
            --scopes "$BOT3_SCOPES" || {
            fail "Failed to onboard $BOT3_ID"
            return 1
        }
        pass "$BOT3_ID onboarded successfully!"
    else
        fail "Cannot find token for $BOT3_ID. Make sure bot is connected to BCS."
        return 1
    fi

    pass "All bots onboarded!"
}

# ============================================================================
# Clean Profile Directories
# ============================================================================

clean_profiles() {
    info "Cleaning profile directories..."
    rm -rf "$HOME/.openclaw-${BOT1_PROFILE}" 2>/dev/null || true
    rm -rf "$HOME/.openclaw-${BOT2_PROFILE}" 2>/dev/null || true
    rm -rf "$HOME/.openclaw-${BOT3_PROFILE}" 2>/dev/null || true
    # Only clean bot data dirs, preserve logs (BCS may be writing to bcs.log)
    for d in "$BOTS_BASE_DIR"/*/; do
        [ -d "$d" ] && [ "$(basename "$d")" != "logs" ] && rm -rf "$d" 2>/dev/null || true
    done
    pass "Profile directories cleaned"
}

# ============================================================================
# Link BCN Plugin
# ============================================================================

link_bcn_plugin() {
    local project_bcn_path="$PROJECT_ROOT/submodules/openclaw-channel-bcn"
    local linked_count=0

    # Try system extensions directory
    local system_ext_dir="/opt/homebrew/lib/node_modules/openclaw/extensions"
    local system_bcn_link="$system_ext_dir/openclaw-channel-bcn"

    if [ -d "$system_ext_dir" ]; then
        if [ -L "$system_bcn_link" ]; then
            local current_target=$(readlink "$system_bcn_link")
            if [ "$current_target" = "$project_bcn_path" ]; then
                pass "BCN plugin already linked at $system_bcn_link"
            else
                info "Replacing existing link at $system_bcn_link"
                rm -f "$system_bcn_link"
                ln -s "$project_bcn_path" "$system_bcn_link"
                pass "BCN plugin linked: $system_bcn_link -> $project_bcn_path"
            fi
        elif [ -d "$system_bcn_link" ]; then
            info "Removing existing directory at $system_bcn_link"
            rm -rf "$system_bcn_link"
            ln -s "$project_bcn_path" "$system_bcn_link"
            pass "BCN plugin linked: $system_bcn_link -> $project_bcn_path"
        else
            ln -s "$project_bcn_path" "$system_bcn_link"
            pass "BCN plugin linked: $system_bcn_link -> $project_bcn_path"
        fi
        linked_count=$((linked_count + 1))
    else
        warn "System extensions directory not found: $system_ext_dir"
    fi

    # Try user extensions directory
    local user_ext_dir="$HOME/.openclaw/extensions"
    local user_bcn_link="$user_ext_dir/openclaw-channel-bcn"

    if [ -d "$user_ext_dir" ]; then
        if [ -L "$user_bcn_link" ]; then
            local current_target=$(readlink "$user_bcn_link")
            if [ "$current_target" = "$project_bcn_path" ]; then
                pass "BCN plugin already linked at $user_bcn_link"
            else
                info "Replacing existing link at $user_bcn_link"
                rm -f "$user_bcn_link"
                ln -s "$project_bcn_path" "$user_bcn_link"
                pass "BCN plugin linked: $user_bcn_link -> $project_bcn_path"
            fi
        elif [ -d "$user_bcn_link" ]; then
            info "Removing existing directory at $user_bcn_link"
            rm -rf "$user_bcn_link"
            ln -s "$project_bcn_path" "$user_bcn_link"
            pass "BCN plugin linked: $user_bcn_link -> $project_bcn_path"
        else
            ln -s "$project_bcn_path" "$user_bcn_link"
            pass "BCN plugin linked: $user_bcn_link -> $project_bcn_path"
        fi
        linked_count=$((linked_count + 1))
    else
        info "User extensions directory not found: $user_ext_dir (this is OK)"
    fi

    if [ $linked_count -eq 0 ]; then
        fail "Could not link BCN plugin to any extensions directory"
        return 1
    fi
}

# ============================================================================
# Main
# ============================================================================

case "${1:-start}" in
    start)
        info "Setting up OpenClaw profile directories..."

        # Link BCN plugin to system extensions
        link_bcn_plugin

        # Clean old data
        clean_profiles
        mkdir -p "$LOG_DIR"

        # Setup Bot 1: reviewer demo
        setup_profile_dir "$BOT1_ID" "$BOT1_PROFILE" "$BOT1_PORT" \
            "$BOT1_SUMMARY" "$BOT1_DOMAINS" "$BOT1_SKILLS" "$BOT1_SCOPES" \
            "你是审理 Bot。
你负责审核文档、合同和规则符合性。" \
            "- 不访问私有数据
- 审核时关注规则、条款和合规性" \
            "## 规则库
- 合同审核要点
- 合规性检查清单" \
            "$BOT1_GATEWAY_TOKEN" \
            "$BOT1_DINGTALK_CLIENT_ID" \
            "$BOT1_DINGTALK_CLIENT_SECRET" \
            "$BOT1_DINGTALK_ROBOT_CODE" \
            "$BOT1_DINGTALK_CORP_ID" \
            "$BOT1_DINGTALK_AGENT_ID"

        # Setup Bot 2: legal demo
        setup_profile_dir "$BOT2_ID" "$BOT2_PROFILE" "$BOT2_PORT" \
            "$BOT2_SUMMARY" "$BOT2_DOMAINS" "$BOT2_SKILLS" "$BOT2_SCOPES" \
            "你是法务 Bot。
你负责合规、条款风险和法律审查建议。" \
            "- 提供法律与合规建议
- 不提供无依据的业务承诺" \
            "## 重点
- 新支付功能需要关注合规风险和条款约束" \
            "$BOT2_GATEWAY_TOKEN" \
            "$BOT2_DINGTALK_CLIENT_ID" \
            "$BOT2_DINGTALK_CLIENT_SECRET" \
            "$BOT2_DINGTALK_ROBOT_CODE" \
            "$BOT2_DINGTALK_CORP_ID" \
            "$BOT2_DINGTALK_AGENT_ID"

        # Setup Bot 3: database demo
        setup_profile_dir "$BOT3_ID" "$BOT3_PROFILE" "$BOT3_PORT" \
            "$BOT3_SUMMARY" "$BOT3_DOMAINS" "$BOT3_SKILLS" "$BOT3_SCOPES" \
            "你是 DBA Bot。
你负责数据库故障排查、性能优化和数据库架构建议。" \
            "- 优先从数据库角度给出专业分析
- 关注死锁、锁等待、慢查询、事务冲突" \
            "## 数据库知识
- 常见死锁原因包括事务加锁顺序不一致
- 排查重点包括锁等待链、事务持锁时间、SQL执行路径" \
            "$BOT3_GATEWAY_TOKEN" \
            "$BOT3_DINGTALK_CLIENT_ID" \
            "$BOT3_DINGTALK_CLIENT_SECRET" \
            "$BOT3_DINGTALK_ROBOT_CODE" \
            "$BOT3_DINGTALK_CORP_ID" \
            "$BOT3_DINGTALK_AGENT_ID"

        echo ""
        info "Starting OpenClaw bots..."

        BOT1_PID=$(start_openclaw "$BOT1_ID" "$BOT1_PROFILE" "$BOT1_PORT" "$LOG_DIR/${BOT1_ID}.log") || exit 1
        BOT2_PID=$(start_openclaw "$BOT2_ID" "$BOT2_PROFILE" "$BOT2_PORT" "$LOG_DIR/${BOT2_ID}.log") || exit 1
        BOT3_PID=$(start_openclaw "$BOT3_ID" "$BOT3_PROFILE" "$BOT3_PORT" "$LOG_DIR/${BOT3_ID}.log") || exit 1

        echo ""
        pass "All OpenClaw bots started successfully!"
        info "Logs: $LOG_DIR/"
        info "Press Ctrl+C to stop all bots"

        # Wait forever
        wait
        ;;

    stop)
        stop_all false
        ;;

    force-stop|clean)
        stop_all true
        clean_profiles
        ;;

    status)
        show_status
        ;;

    onboard)
        cmd_onboard
        ;;

    *)
        echo "Usage: $0 {start|stop|force-stop|status|onboard}"
        echo ""
        echo "Commands:"
        echo "  start       - Setup and start all three OpenClaw bots"
        echo "  stop        - Stop the three bots by port"
        echo "  force-stop  - Force stop ALL openclaw processes and clean profiles"
        echo "  status      - Show bot status"
        echo "  onboard     - Complete bot onboarding with BCS"
        exit 1
        ;;
esac
