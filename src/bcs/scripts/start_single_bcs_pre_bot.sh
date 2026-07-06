#!/bin/bash
# Start one local OpenClaw gateway connected to BCS pre via BCN.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1" >&2; exit 1; }
info() { echo -e "  ${CYAN}→${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }

COMMAND="${1:-start}"
if [ $# -gt 0 ]; then
    shift
fi

BCS_WS_URL="${BCS_WS_URL:-wss://bcs-pre.example.com/ws/bot}"
BCS_HTTP_URL="${BCS_HTTP_URL:-https://bcs-pre.example.com}"
PROFILE="${PROFILE:-bcs_pre_single}"
BOT_ID="${BOT_ID:-PreBot}"
BOT_NAME="${BOT_NAME:-$BOT_ID}"
PORT="${PORT:-30101}"
SUMMARY="${SUMMARY:-Single OpenClaw bot connected to BCS pre}"
DOMAINS="${DOMAINS:-pre,bcs}"
SKILLS="${SKILLS:-chat}"
SCOPES="${SCOPES:-pre}"
VISIBILITY="${VISIBILITY:-public}"
GATEWAY_TOKEN="${GATEWAY_TOKEN:-bcs_pre_single_token_2026}"
BCS_CLI="${BCS_CLI:-$PROJECT_ROOT/target/debug/bcs-cli}"
BASE_DIR="${BASE_DIR:-$PROJECT_ROOT/bcs_single_pre_dir}"
LOG_DIR="${LOG_DIR:-$BASE_DIR/logs}"
PID_DIR="${PID_DIR:-$BASE_DIR/pids}"
TOKEN_WAIT_ATTEMPTS="${TOKEN_WAIT_ATTEMPTS:-30}"
TOKEN_WAIT_INTERVAL="${TOKEN_WAIT_INTERVAL:-2}"
OPENCLAW_HEALTH_ATTEMPTS="${OPENCLAW_HEALTH_ATTEMPTS:-30}"
OPENCLAW_HEALTH_INTERVAL="${OPENCLAW_HEALTH_INTERVAL:-1}"
BCS_COOKIE_VALUE="${BCS_COOKIE:-}"
BCN_PLUGIN_PACKAGE="${BCN_PLUGIN_PACKAGE:-@inclusionai/openclaw-channel-bcs}"
BCN_PLUGIN_VERSION="${BCN_PLUGIN_VERSION:-0.0.90011710437-dev.2}"
BCN_PLUGIN_SPEC="${BCN_PLUGIN_SPEC:-}"
PLUGIN_CACHE_DIR="${PLUGIN_CACHE_DIR:-$BASE_DIR/plugins}"
NPM_CLIENT="${NPM_CLIENT:-}"
BCN_PLUGIN_LOAD_DIR="${BCN_PLUGIN_LOAD_DIR:-}"
BCN_PLUGIN_SRC_DIR="${BCN_PLUGIN_SRC_DIR:-}"
if [ -n "$BCN_PLUGIN_LOAD_DIR" ] || [ -n "$BCN_PLUGIN_SRC_DIR" ]; then
    BCN_PLUGIN_MANAGED=0
else
    BCN_PLUGIN_MANAGED=1
fi
MODEL_PROVIDER_ID="${OPENCLAW_OPENAI_PROVIDER_ID:-openai_compatible}"
MODEL_BASE_URL="${OPENCLAW_OPENAI_BASE_URL:-https://api.openai.com/v1}"
MODEL_API_KEY="${OPENCLAW_OPENAI_API_KEY:-}"
MODEL_ID="${OPENCLAW_OPENAI_MODEL_ID:-gpt-4.1-mini}"
CLEAN_PROFILE=0
RUN_ONBOARD=1
PRE_AUTH_CHECK_DONE=0

usage() {
    cat <<EOF
Usage: $0 {start|onboard|check-auth|write-config|install-plugin|plugin-path|plan-onboard|stop|status} [options]

Commands:
  start          Write profile config, start OpenClaw, wait for token, onboard to BCS pre
  onboard        Onboard the existing profile/session to BCS pre
  check-auth     Check whether BCS pre HTTP API accepts current Cookie/auth
  write-config   Only write the OpenClaw profile config
  install-plugin Install/check the BCN plugin npm package
  plugin-path    Print the BCN plugin load path that will be written to openclaw.json
  plan-onboard   Print the onboard command without executing it
  stop           Stop the OpenClaw gateway for this profile/port
  status         Show local gateway and BCS session status

Options:
  --profile NAME          OpenClaw profile name (default: $PROFILE)
  --bot-id ID             BCS bot id/name used for onboarding (default: $BOT_ID)
  --bot-name NAME         Display name in BCN config (default: bot id)
  --port PORT             Local OpenClaw gateway port (default: $PORT)
  --summary TEXT          Bot summary
  --domains CSV           Capabilities domains CSV
  --skills CSV            Capabilities skills CSV
  --scopes CSV            Capabilities scopes CSV
  --visibility VALUE      public/protected/private/skip after onboard (default: $VISIBILITY)
  --bcs-ws-url URL        BCN WebSocket URL (default: $BCS_WS_URL)
  --bcs-http-url URL      BCS HTTP API URL for bcs-cli (default: $BCS_HTTP_URL)
  --bcs-cli PATH          bcs-cli path (default: $BCS_CLI)
  --bcn-package SPEC      BCN plugin npm package spec (default: $BCN_PLUGIN_PACKAGE@$BCN_PLUGIN_VERSION)
  --bcn-plugin-version V  BCN plugin npm package version (default: $BCN_PLUGIN_VERSION)
  --plugin-cache-dir DIR  Directory used for npm package install (default: $PLUGIN_CACHE_DIR)
  --npm-client CMD        npm-compatible client to install plugin (default: npm)
  --bcn-plugin PATH       Local BCN plugin source/load directory override; disables npm install
  --gateway-token TOKEN   OpenClaw gateway token
  --token TOKEN           Explicit bot token for onboard/plan-onboard
  --cookie COOKIE         Cookie header for BCN WebSocket and bcs-cli HTTP requests
  --model-api-key KEY     Model API key, or set OPENCLAW_OPENAI_API_KEY
  --model-id MODEL        Model id (default: $MODEL_ID)
  --clean                 Remove the profile before writing config
  --no-onboard            For start: skip bcs-cli onboard
  -h, --help              Show this help

Environment overrides use the same option names in uppercase where practical,
for example BCS_WS_URL, BCS_HTTP_URL, PROFILE, BOT_ID, PORT, BCS_COOKIE,
BCN_PLUGIN_SPEC, PLUGIN_CACHE_DIR, NPM_CLIENT.
EOF
}

EXPLICIT_TOKEN=""
while [ $# -gt 0 ]; do
    case "$1" in
        --profile) PROFILE="$2"; shift 2 ;;
        --bot-id) BOT_ID="$2"; shift 2 ;;
        --bot-name) BOT_NAME="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --summary) SUMMARY="$2"; shift 2 ;;
        --domains) DOMAINS="$2"; shift 2 ;;
        --skills) SKILLS="$2"; shift 2 ;;
        --scopes) SCOPES="$2"; shift 2 ;;
        --visibility) VISIBILITY="$2"; shift 2 ;;
        --bcs-ws-url) BCS_WS_URL="$2"; shift 2 ;;
        --bcs-http-url) BCS_HTTP_URL="$2"; shift 2 ;;
        --bcs-cli) BCS_CLI="$2"; shift 2 ;;
        --bcn-package) BCN_PLUGIN_SPEC="$2"; shift 2 ;;
        --bcn-plugin-version) BCN_PLUGIN_VERSION="$2"; shift 2 ;;
        --plugin-cache-dir) PLUGIN_CACHE_DIR="$2"; shift 2 ;;
        --npm-client) NPM_CLIENT="$2"; shift 2 ;;
        --bcn-plugin)
            BCN_PLUGIN_SRC_DIR="$2"
            BCN_PLUGIN_LOAD_DIR=""
            BCN_PLUGIN_MANAGED=0
            shift 2
            ;;
        --gateway-token) GATEWAY_TOKEN="$2"; shift 2 ;;
        --token) EXPLICIT_TOKEN="$2"; shift 2 ;;
        --cookie) BCS_COOKIE_VALUE="$2"; shift 2 ;;
        --model-api-key) MODEL_API_KEY="$2"; shift 2 ;;
        --model-id) MODEL_ID="$2"; shift 2 ;;
        --clean) CLEAN_PROFILE=1; shift ;;
        --no-onboard) RUN_ONBOARD=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Unknown option: $1" ;;
    esac
done

if [ -z "$BCN_PLUGIN_SPEC" ]; then
    BCN_PLUGIN_SPEC="$BCN_PLUGIN_PACKAGE@$BCN_PLUGIN_VERSION"
fi

if [ "$BCN_PLUGIN_MANAGED" = "1" ]; then
    BCN_PLUGIN_LOAD_DIR="$PLUGIN_CACHE_DIR/node_modules/$BCN_PLUGIN_PACKAGE"
elif [ -z "$BCN_PLUGIN_LOAD_DIR" ]; then
    [ -n "$BCN_PLUGIN_SRC_DIR" ] || fail "Local BCN plugin path is empty"
    BCN_PLUGIN_PACKAGE_DIR="$BCN_PLUGIN_SRC_DIR/package"
    if [ -f "$BCN_PLUGIN_PACKAGE_DIR/openclaw.plugin.json" ] && [ -f "$BCN_PLUGIN_PACKAGE_DIR/dist/esm/index.js" ]; then
        BCN_PLUGIN_LOAD_DIR="$BCN_PLUGIN_PACKAGE_DIR"
    else
        BCN_PLUGIN_LOAD_DIR="$BCN_PLUGIN_SRC_DIR"
    fi
fi

PROFILE_DIR="$HOME/.openclaw-${PROFILE}"
WORKSPACE_DIR="$PROFILE_DIR/workspace"
SESSION_FILE="$PROFILE_DIR/.bcs/session.json"
PID_FILE="$PID_DIR/${PROFILE}.pid"
LOG_FILE="$LOG_DIR/${PROFILE}.log"

json_escape() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//$'\n'/\\n}"
    printf '%s' "$value"
}

json_string() {
    printf '"%s"' "$(json_escape "$1")"
}

trim() {
    sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

json_array_from_csv() {
    local raw="$1"
    local first=1
    local item trimmed
    printf '['
    if [ -n "$raw" ]; then
        IFS=',' read -r -a items <<< "$raw"
        for item in "${items[@]}"; do
            trimmed="$(printf '%s' "$item" | trim)"
            [ -n "$trimmed" ] || continue
            if [ "$first" -eq 0 ]; then
                printf ','
            fi
            json_string "$trimmed"
            first=0
        done
    fi
    printf ']'
}

require_openclaw() {
    command -v openclaw >/dev/null 2>&1 || fail "openclaw command not found"
}

require_bcs_cli() {
    [ -x "$BCS_CLI" ] || fail "bcs-cli not found or not executable: $BCS_CLI"
}

choose_npm_client() {
    if [ -n "$NPM_CLIENT" ]; then
        command -v "$NPM_CLIENT" >/dev/null 2>&1 || fail "npm client not found: $NPM_CLIENT"
        printf '%s' "$NPM_CLIENT"
        return 0
    fi

    if command -v npm >/dev/null 2>&1; then
        printf 'npm'
        return 0
    fi

    fail "npm was not found; install Node.js with npm or pass --npm-client"
}

ensure_bcn_plugin_package() {
    if [ "$BCN_PLUGIN_MANAGED" != "1" ]; then
        return 0
    fi

    if [ -f "$BCN_PLUGIN_LOAD_DIR/openclaw.plugin.json" ] && [ -f "$BCN_PLUGIN_LOAD_DIR/dist/esm/index.js" ]; then
        pass "BCN plugin package ready: $BCN_PLUGIN_LOAD_DIR"
        return 0
    fi

    local npm_client
    npm_client="$(choose_npm_client)"
    mkdir -p "$PLUGIN_CACHE_DIR"

    info "Installing BCN plugin package: $BCN_PLUGIN_SPEC"
    info "Install directory: $PLUGIN_CACHE_DIR"
    (cd "$PLUGIN_CACHE_DIR" && "$npm_client" install --no-save "$BCN_PLUGIN_SPEC") >&2
}

require_bcn_plugin() {
    ensure_bcn_plugin_package
    [ -f "$BCN_PLUGIN_LOAD_DIR/openclaw.plugin.json" ] || fail "BCN plugin manifest not found: $BCN_PLUGIN_LOAD_DIR/openclaw.plugin.json"
    [ -f "$BCN_PLUGIN_LOAD_DIR/dist/esm/index.js" ] || fail "BCN plugin build output not found: $BCN_PLUGIN_LOAD_DIR/dist/esm/index.js"
}

run_bcs_cli_pre() {
    local -a env_args=(
        "BOT_DATA_DIR=$PROFILE_DIR"
        "MOLTIS_BCS_URL=$BCS_HTTP_URL"
        "tc_sdb_nenv=production"
    )
    if [ -n "$BCS_COOKIE_VALUE" ]; then
        env_args+=("BCS_COOKIE=$BCS_COOKIE_VALUE")
    fi
    env "${env_args[@]}" "$BCS_CLI" "$@"
}

check_pre_auth() {
    if [ "$PRE_AUTH_CHECK_DONE" = "1" ]; then
        return 0
    fi

    local body_file http_status
    body_file="$(mktemp)"

    local -a curl_args=(
        -sS
        --max-time
        10
        -o
        "$body_file"
        -w
        "%{http_code}"
    )
    if [ -n "$BCS_COOKIE_VALUE" ]; then
        curl_args+=(-H "Cookie: $BCS_COOKIE_VALUE")
    fi
    curl_args+=("$BCS_HTTP_URL/health")

    if ! http_status="$(curl "${curl_args[@]}")"; then
        rm -f "$body_file"
        fail "Failed to reach BCS pre health endpoint: $BCS_HTTP_URL/health"
    fi

    if grep -Eq 'USER_NOT_LOGIN|buserviceErrorCode' "$body_file"; then
        rm -f "$body_file"
        fail "BCS pre HTTP API requires Buservice login. Provide a valid Cookie via BCS_COOKIE or --cookie before onboarding."
    fi

    if [[ ! "$http_status" =~ ^2 ]]; then
        echo "BCS pre health response:" >&2
        sed -n '1,40p' "$body_file" >&2
        rm -f "$body_file"
        fail "BCS pre health check failed with HTTP $http_status"
    fi

    rm -f "$body_file"
    PRE_AUTH_CHECK_DONE=1
    pass "BCS pre HTTP auth check passed: $BCS_HTTP_URL"
}

write_workspace_files() {
    mkdir -p "$WORKSPACE_DIR"
    printf 'You are %s, a single OpenClaw bot connected to BCS pre.\n' "$BOT_NAME" > "$WORKSPACE_DIR/SOUL.md"
    printf 'Use the BCS pre environment for collaboration tests. Keep actions scoped to the current task.\n' > "$WORKSPACE_DIR/RULES.md"
    printf 'Profile: %s\nBCS WebSocket: %s\nBCS HTTP: %s\n' "$PROFILE" "$BCS_WS_URL" "$BCS_HTTP_URL" > "$WORKSPACE_DIR/MEMORY.md"
}

write_config() {
    if [ "$CLEAN_PROFILE" = "1" ]; then
        info "Cleaning profile directory: $PROFILE_DIR"
        rm -rf "$PROFILE_DIR"
    fi

    mkdir -p "$PROFILE_DIR" "$WORKSPACE_DIR" "$LOG_DIR" "$PID_DIR"
    write_workspace_files

    local bot_id_json bot_name_json summary_json workspace_json bcs_url_json plugin_json gateway_token_json
    local model_provider_id_json model_base_url_json model_api_key_json model_id_json model_ref_json
    bot_id_json="$(json_string "$BOT_ID")"
    bot_name_json="$(json_string "$BOT_NAME")"
    summary_json="$(json_string "$SUMMARY")"
    workspace_json="$(json_string "$WORKSPACE_DIR")"
    bcs_url_json="$(json_string "$BCS_WS_URL")"
    plugin_json="$(json_string "$BCN_PLUGIN_LOAD_DIR")"
    gateway_token_json="$(json_string "$GATEWAY_TOKEN")"
    model_provider_id_json="$(json_string "$MODEL_PROVIDER_ID")"
    model_base_url_json="$(json_string "$MODEL_BASE_URL")"
    model_api_key_json="$(json_string "$MODEL_API_KEY")"
    model_id_json="$(json_string "$MODEL_ID")"
    model_ref_json="$(json_string "$MODEL_PROVIDER_ID/$MODEL_ID")"

    cat > "$PROFILE_DIR/openclaw.json" <<EOF
{
  "meta": {
    "lastTouchedVersion": "2026.3.12"
  },
  "models": {
    "mode": "merge",
    "providers": {
      $model_provider_id_json: {
        "baseUrl": $model_base_url_json,
        "apiKey": $model_api_key_json,
        "auth": "api-key",
        "api": "openai-completions",
        "models": [
          {
            "id": $model_id_json,
            "name": $model_id_json,
            "api": "openai-completions",
            "reasoning": true,
            "input": ["text"],
            "cost": {
              "input": 0.0025,
              "output": 0.01,
              "cacheRead": 0,
              "cacheWrite": 0
            },
            "contextWindow": 10000000,
            "maxTokens": 65536
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": $model_ref_json
      },
      "models": {
        $model_ref_json: {
          "alias": $model_id_json
        }
      },
      "workspace": $workspace_json,
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
  "skills": {
    "allowBundled": []
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
      "bcsUrl": $bcs_url_json,
      "botId": $bot_id_json,
      "botName": $bot_name_json,
      "capabilities": {
        "summary": $summary_json,
        "domains": $(json_array_from_csv "$DOMAINS"),
        "skills": $(json_array_from_csv "$SKILLS"),
        "scopes": $(json_array_from_csv "$SCOPES")
      },
      "heartbeatIntervalMs": 60000,
      "reconnectIntervalMs": 5000,
      "connectionTimeoutMs": 30000
    }
  },
  "gateway": {
    "port": $PORT,
    "mode": "local",
    "bind": "loopback",
    "controlUi": {
      "dangerouslyDisableDeviceAuth": true
    },
    "auth": {
      "mode": "token",
      "token": $gateway_token_json
    },
    "tailscale": {
      "mode": "off",
      "resetOnExit": false
    }
  },
  "plugins": {
    "load": {
      "paths": [
        $plugin_json
      ]
    },
    "entries": {
      "openclaw-channel-bcn": {
        "enabled": true
      }
    }
  }
}
EOF

    if [ -n "$BCS_COOKIE_VALUE" ]; then
        # Keep the cookie out of openclaw.json; pass it via environment.
        export BCS_COOKIE="$BCS_COOKIE_VALUE"
    fi

    if [ -f "$HOME/.config/moltis/provider_keys.json" ]; then
        mkdir -p "$PROFILE_DIR/config"
        cp "$HOME/.config/moltis/provider_keys.json" "$PROFILE_DIR/config/" 2>/dev/null || true
    fi

    pass "OpenClaw profile config written: $PROFILE_DIR/openclaw.json"
}

get_session_field() {
    local field="$1"
    [ -f "$SESSION_FILE" ] || return 0
    sed -n "s/.*\"$field\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p" "$SESSION_FILE" | head -1
}

get_bot_token() {
    local session_url token
    session_url="$(get_session_field "bcs_url")"
    token="$(get_session_field "token")"

    if [ -z "$token" ]; then
        return 0
    fi
    if [ "$session_url" != "$BCS_WS_URL" ]; then
        return 0
    fi
    printf '%s' "$token"
}

wait_for_token() {
    local token=""
    local warned_mismatch=0

    for _ in $(seq 1 "$TOKEN_WAIT_ATTEMPTS"); do
        token="$(get_bot_token)"
        if [ -n "$token" ]; then
            printf '%s' "$token"
            return 0
        fi

        if [ -f "$SESSION_FILE" ] && [ "$warned_mismatch" = "0" ]; then
            local session_url
            session_url="$(get_session_field "bcs_url")"
            if [ -n "$session_url" ] && [ "$session_url" != "$BCS_WS_URL" ]; then
                warn "Ignoring stale session for $session_url; waiting for $BCS_WS_URL"
                warned_mismatch=1
            fi
        fi

        sleep "$TOKEN_WAIT_INTERVAL"
    done

    return 1
}

stop_existing_on_port() {
    local pids
    pids="$(lsof -ti :"$PORT" 2>/dev/null || true)"
    if [ -n "$pids" ]; then
        warn "Stopping existing process on port $PORT: $pids"
        echo "$pids" | xargs kill 2>/dev/null || true
        sleep 1
        pids="$(lsof -ti :"$PORT" 2>/dev/null || true)"
        if [ -n "$pids" ]; then
            echo "$pids" | xargs kill -9 2>/dev/null || true
        fi
    fi
}

start_openclaw() {
    require_openclaw
    require_bcn_plugin
    write_config
    stop_existing_on_port

    info "Starting OpenClaw ($BOT_ID) on port $PORT with profile $PROFILE..."
    local -a env_args=(
        "NODE_TLS_REJECT_UNAUTHORIZED=0"
        "BCS_IGNORE_CREDENTIALS=1"
        "OPENCLAW_GATEWAY_TOKEN="
        "OPENCLAW_DATA_DIR=$PROFILE_DIR"
        "MOLTIS_BCS_URL=$BCS_HTTP_URL"
        "tc_sdb_nenv=production"
    )
    if [ -n "$BCS_COOKIE_VALUE" ]; then
        env_args+=("BCS_COOKIE=$BCS_COOKIE_VALUE")
    fi
    env "${env_args[@]}" openclaw --profile "$PROFILE" gateway run --port "$PORT" > "$LOG_FILE" 2>&1 &

    local pid=$!
    echo "$pid" > "$PID_FILE"

    for _ in $(seq 1 "$OPENCLAW_HEALTH_ATTEMPTS"); do
        if curl -s "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
            pass "OpenClaw started on port $PORT (PID $pid)"
            return 0
        fi
        sleep "$OPENCLAW_HEALTH_INTERVAL"
    done

    fail "OpenClaw failed to start; check $LOG_FILE"
}

plan_onboard() {
    local token="${EXPLICIT_TOKEN:-<token-from-$SESSION_FILE>}"
    printf 'BOT_DATA_DIR=%q MOLTIS_BCS_URL=%q tc_sdb_nenv=production ' "$PROFILE_DIR" "$BCS_HTTP_URL"
    if [ -n "$BCS_COOKIE_VALUE" ]; then
        printf 'BCS_COOKIE=%q ' "$BCS_COOKIE_VALUE"
    fi
    printf '%q onboard --token %q --name %q --summary %q --domains %q --skills %q --scopes %q\n' \
        "$BCS_CLI" "$token" "$BOT_ID" "$SUMMARY" "$DOMAINS" "$SKILLS" "$SCOPES"
}

onboard() {
    require_bcs_cli
    check_pre_auth

    local token="$EXPLICIT_TOKEN"
    if [ -z "$token" ]; then
        info "Waiting for BCN session token in $SESSION_FILE..."
        token="$(wait_for_token)" || fail "Cannot find a pre BCS token in $SESSION_FILE"
    fi

    info "Onboarding $BOT_ID to BCS pre ($BCS_HTTP_URL)..."
    local onboard_output
    if ! onboard_output="$(run_bcs_cli_pre onboard \
            --token "$token" \
            --name "$BOT_ID" \
            --summary "$SUMMARY" \
            --domains "$DOMAINS" \
            --skills "$SKILLS" \
            --scopes "$SCOPES" 2>&1)"; then
        printf '%s\n' "$onboard_output" >&2
        if printf '%s\n' "$onboard_output" | grep -Fq "Invalid onboard response"; then
            fail "BCS pre returned a non-BCS onboard response. This is usually Buservice login JSON; provide a valid Cookie via BCS_COOKIE or --cookie."
        fi
        fail "bcs-cli onboard failed"
    fi
    printf '%s\n' "$onboard_output"

    if [ "$VISIBILITY" != "skip" ]; then
        info "Setting visibility=$VISIBILITY for $BOT_ID..."
        local visibility_output
        if ! visibility_output="$(run_bcs_cli_pre visibility set --value "$VISIBILITY" 2>&1)"; then
            printf '%s\n' "$visibility_output" >&2
            fail "bcs-cli visibility set failed"
        fi
        printf '%s\n' "$visibility_output"
    fi

    pass "$BOT_ID onboarded to BCS pre"
}

stop_bot() {
    local stopped=0
    if [ -f "$PID_FILE" ]; then
        local pid
        pid="$(cat "$PID_FILE" 2>/dev/null || true)"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            info "Stopping OpenClaw PID $pid"
            kill "$pid" 2>/dev/null || true
            stopped=1
        fi
        rm -f "$PID_FILE"
    fi

    local pids
    pids="$(lsof -ti :"$PORT" 2>/dev/null || true)"
    if [ -n "$pids" ]; then
        info "Stopping processes on port $PORT: $pids"
        echo "$pids" | xargs kill 2>/dev/null || true
        sleep 1
        pids="$(lsof -ti :"$PORT" 2>/dev/null || true)"
        if [ -n "$pids" ]; then
            echo "$pids" | xargs kill -9 2>/dev/null || true
        fi
        stopped=1
    fi

    if [ "$stopped" = "1" ]; then
        pass "OpenClaw stopped"
    else
        warn "No OpenClaw process found for profile=$PROFILE port=$PORT"
    fi
}

status() {
    echo ""
    info "Single BCS pre bot status"
    if curl -s "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
        pass "OpenClaw gateway running: http://127.0.0.1:$PORT"
    else
        warn "OpenClaw gateway not running on port $PORT"
    fi

    if [ -f "$SESSION_FILE" ]; then
        local session_url bot_uuid token
        session_url="$(get_session_field "bcs_url")"
        bot_uuid="$(get_session_field "bot_uuid")"
        token="$(get_session_field "token")"
        pass "Session file: $SESSION_FILE"
        info "Session bcs_url: ${session_url:-<missing>}"
        info "Session bot_uuid: ${bot_uuid:-<missing>}"
        if [ -n "$token" ]; then
            info "Session token: ${token:0:8}..."
        else
            warn "Session token missing"
        fi
    else
        warn "Session file not found: $SESSION_FILE"
    fi

    if [ "$BCN_PLUGIN_MANAGED" = "1" ]; then
        info "BCN plugin package: $BCN_PLUGIN_SPEC"
        info "BCN plugin cache: $PLUGIN_CACHE_DIR"
    else
        info "BCN plugin local override: $BCN_PLUGIN_LOAD_DIR"
    fi
    info "Log file: $LOG_FILE"
    echo ""
}

case "$COMMAND" in
    start)
        if [ "$RUN_ONBOARD" = "1" ]; then
            check_pre_auth
        fi
        start_openclaw
        if [ "$RUN_ONBOARD" = "1" ]; then
            onboard
        else
            warn "Skipping onboard because --no-onboard was set"
        fi
        echo ""
        info "OpenClaw gateway: http://127.0.0.1:$PORT"
        info "BCS WebSocket: $BCS_WS_URL"
        info "BCS HTTP: $BCS_HTTP_URL"
        info "Profile: $PROFILE_DIR"
        info "Log: $LOG_FILE"
        ;;
    onboard)
        onboard
        ;;
    check-auth)
        check_pre_auth
        ;;
    write-config)
        write_config
        ;;
    install-plugin)
        require_bcn_plugin
        pass "BCN plugin installed/verified: $BCN_PLUGIN_LOAD_DIR"
        ;;
    plugin-path)
        printf '%s\n' "$BCN_PLUGIN_LOAD_DIR"
        ;;
    plan-onboard)
        plan_onboard
        ;;
    stop)
        stop_bot
        ;;
    status)
        status
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        usage >&2
        fail "Unknown command: $COMMAND"
        ;;
esac
