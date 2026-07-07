#!/bin/bash
# Start BCS and 5 OpenClaw demo bots: CEO, Product, Engineering, Verification, Customer
# 使用 OpenClaw 的 BCN plugin 连接到 BCS

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$PROJECT_ROOT/../.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOTS_BASE_DIR="$PROJECT_ROOT/bcs_bots_test_dir"
BCS_PORT="${BCS_PORT:-21000}"
BCS_URL="ws://127.0.0.1:${BCS_PORT}/ws/bot"
BCS_API_BASE_URL="${BCS_API_BASE_URL:-http://127.0.0.1:${BCS_PORT}}"
BCS_CONFIG_DIR="${BCS_CONFIG_DIR:-$PROJECT_ROOT/configs}"
BCS_BOTS_PRESERVE_FILES="${BCS_BOTS_PRESERVE_FILES:-1}"
BCS_BOTS_DETACHED="${BCS_BOTS_DETACHED:-0}"
BCS_BOT_PORT_AUTO="${BCS_BOT_PORT_AUTO:-0}"
OPENCLAW_MODEL_CONFIG_SOURCE="${OPENCLAW_MODEL_CONFIG_SOURCE:-$HOME/.openclaw/openclaw.json}"
OPENCLAW_PROFILE_ROOT="${OPENCLAW_PROFILE_ROOT:-$HOME}"
OPENCLAW_PROFILE_PREFIX="${OPENCLAW_PROFILE_PREFIX-.openclaw-}"
OPENCLAW_WORKSPACE_ROOT="${OPENCLAW_WORKSPACE_ROOT:-$BOTS_BASE_DIR}"
OPENCLAW_WORKSPACE_LAYOUT="${OPENCLAW_WORKSPACE_LAYOUT:-profile-source}"
OPENCLAW_EXTENSIONS_ROOT="${OPENCLAW_EXTENSIONS_ROOT:-$HOME/.openclaw/extensions}"
OPENCLAW_EXTENSIONS_REPLACE_LINKS="${OPENCLAW_EXTENSIONS_REPLACE_LINKS:-0}"
OPENCLAW_LOG_ROOT="${OPENCLAW_LOG_ROOT:-$BOTS_BASE_DIR/logs}"
FIVE_BOTS_PROFILE_DIR="${FIVE_BOTS_PROFILE_DIR:-$REPO_ROOT/scripts/5bots_profile}"
OPENCLAW_MODELS_JSON=""
OPENCLAW_AGENT_MODEL_FIELDS_JSON="{}"
if [ -z "${MOLTIS_BCS_CONFIG:-}" ]; then
    if [ -f "$BCS_CONFIG_DIR/bcs-config.toml" ]; then
        MOLTIS_BCS_CONFIG="$BCS_CONFIG_DIR/bcs-config.toml"
    elif [ -f "$BCS_CONFIG_DIR/bcs-config-local.toml" ]; then
        MOLTIS_BCS_CONFIG="$BCS_CONFIG_DIR/bcs-config-local.toml"
    fi
fi
export BCS_API_BASE_URL
export BCS_CONFIG_DIR
export MOLTIS_BCS_CONFIG
BCS_CLI="${BCS_CLI_BIN:-$PROJECT_ROOT/target/debug/bcs-cli}"
BCS_ADMIN="$PROJECT_ROOT/target/debug/bcs-admin"
BCS_BIN="${BCS_BIN:-$PROJECT_ROOT/target/debug/bcs}"
# BCN plugin: source (build monorepo tree) or npm (consume installed package)
BCN_PLUGIN_SOURCE="${BCN_PLUGIN_SOURCE:-source}"
BCN_PLUGIN_VERSION="${BCN_PLUGIN_VERSION:-latest}"
BCN_PLUGIN_SRC_DIR="$PROJECT_ROOT/../../src/plugin/packages/openclaw-channel-bcn"
BCN_PLUGIN_PACKAGE_DIR="$BCN_PLUGIN_SRC_DIR/package"
if [ "$BCN_PLUGIN_SOURCE" = "npm" ]; then
    BCN_PLUGIN_LOAD_DIR=""
    for _bcn_cand in \
        "${OPENCLAW_EXTENSIONS_ROOT:-$HOME/.openclaw/extensions}/openclaw-channel-bcn" \
        "$HOME/.openclaw/extensions/openclaw-channel-bcn"; do
        if [ -f "$_bcn_cand/openclaw.plugin.json" ] && [ -f "$_bcn_cand/dist/esm/index.js" ]; then
            BCN_PLUGIN_LOAD_DIR="$_bcn_cand"
            break
        fi
    done
    if [ -z "$BCN_PLUGIN_LOAD_DIR" ]; then
        echo "ERROR: BCN plugin (npm mode) not installed under extensions root; run singlebox setup first" >&2
        exit 1
    fi
elif [ -f "$BCN_PLUGIN_SRC_DIR/openclaw.plugin.json" ] && [ -f "$BCN_PLUGIN_SRC_DIR/dist/esm/index.js" ]; then
    BCN_PLUGIN_LOAD_DIR="$BCN_PLUGIN_SRC_DIR"
elif [ -f "$BCN_PLUGIN_PACKAGE_DIR/openclaw.plugin.json" ] && [ -f "$BCN_PLUGIN_PACKAGE_DIR/dist/esm/index.js" ]; then
    BCN_PLUGIN_LOAD_DIR="$BCN_PLUGIN_PACKAGE_DIR"
else
    BCN_PLUGIN_LOAD_DIR="$BCN_PLUGIN_SRC_DIR"
fi

LOG_DIR="$OPENCLAW_LOG_ROOT"
BCS_LOG="$LOG_DIR/bcs.log"
BCS_BOT_PORTS_FILE="${BCS_BOT_PORTS_FILE:-$LOG_DIR/bcs_bot_ports.env}"

profile_dir_for() {
    local profile="$1"
    printf '%s/%s%s\n' "$OPENCLAW_PROFILE_ROOT" "$OPENCLAW_PROFILE_PREFIX" "$profile"
}

bcs_session_file_for() {
    local profile="$1"
    printf '%s/.bcs/session.json\n' "$(profile_dir_for "$profile")"
}

session_bot_uuid_for() {
    local profile="$1"
    local session_file
    session_file="$(bcs_session_file_for "$profile")"

    [ -f "$session_file" ] || return 0
    command -v jq >/dev/null 2>&1 || return 0

    jq -r 'if (.bot_uuid | type) == "string" then .bot_uuid else empty end' "$session_file" 2>/dev/null | head -n 1
}

workspace_dir_for() {
    local bot_id="$1"
    local profile="$2"
    local profile_source="${3:-$profile}"
    case "$OPENCLAW_WORKSPACE_LAYOUT" in
        profile)
            printf '%s/%s\n' "$OPENCLAW_WORKSPACE_ROOT" "$profile"
            ;;
        profile-source)
            printf '%s/%s/workspace\n' "$OPENCLAW_WORKSPACE_ROOT" "$profile_source"
            ;;
        *)
            printf '%s/%s/workspace\n' "$OPENCLAW_WORKSPACE_ROOT" "$bot_id"
            ;;
    esac
}

# ============================================================================
# Bot Configurations - 基于「一个人 + 一支队」5 bot profile
# ============================================================================

# Bot 1: CEO
BOT1_ID="CEO"
BOT1_NAME="CEO"
BOT1_PROFILE="ceo"
BOT1_PORT="${BOT1_PORT:-30001}"
BOT1_PROFILE_SOURCE="ceo"
BOT1_SUMMARY="CEO，团队总控 Bot，负责把 Chairman 的模糊目标压缩成清晰任务，用第一性原理识别关键约束，调度产品、研发、验证、客服协作，并在冲突中做最终取舍和推进闭环"
BOT1_DOMAINS="strategy,execution,first-principles,orchestration,leadership,resource-allocation,tradeoff,team-coordination,decision-making"
BOT1_SKILLS="goal-framing,first-principles-analysis,task-decomposition,priority-ranking,agent-routing,resource-allocation,risk-framing,tradeoff-analysis,decision-escalation,execution-followup"
BOT1_SCOPES="production"

# Bot 2: Product
BOT2_ID="产品经理"
BOT2_NAME="产品经理"
BOT2_PROFILE="product-manager"
BOT2_PORT="${BOT2_PORT:-30011}"
BOT2_PROFILE_SOURCE="product-manager"
BOT2_SUMMARY="产品经理，产品判断 Bot，负责从用户场景和体验闭环出发定义需求，识别真正痛点，裁剪功能范围，明确不做什么，并把产品判断转成研发和验证可执行的验收标准"
BOT2_DOMAINS="product,user-experience,requirement,prioritization,prd,scope,design-quality,user-value,acceptance-criteria"
BOT2_SKILLS="product-judgment,user-scenario-framing,requirement-clarification,scope-pruning,mvp-definition,ux-review,copy-review,acceptance-criteria-design,non-goal-definition,product-risk-review"
BOT2_SCOPES="production"

# Bot 3: Engineering
BOT3_ID="研发"
BOT3_NAME="研发"
BOT3_PROFILE="engineering"
BOT3_PORT="${BOT3_PORT:-30021}"
BOT3_PROFILE_SOURCE="engineering"
BOT3_SUMMARY="研发，工程实现 Bot，负责把产品目标落到真实代码和系统边界上，评估架构影响、实现复杂度、技术风险和维护成本，优先选择简单、可测试、可演进的工程路径"
BOT3_DOMAINS="engineering,architecture,code,maintainability,implementation,technical-design,code-quality,system-boundary,technical-debt"
BOT3_SKILLS="implementation-planning,architecture-review,code-review,technical-risk-analysis,complexity-control,dependency-analysis,contract-impact-review,testability-review,debugging-plan,maintainability-assessment"
BOT3_SCOPES="production"

# Bot 4: Verification
BOT4_ID="验证"
BOT4_NAME="验证"
BOT4_PROFILE="verification"
BOT4_PORT="${BOT4_PORT:-30031}"
BOT4_PROFILE_SOURCE="verification"
BOT4_SUMMARY="验证，质量验证 Bot，负责把团队结论转成可证伪假设，设计测试、寻找反例、检查边界条件，区分已验证和未验证内容，并在发布或承诺前给出证据化质量门禁"
BOT4_DOMAINS="verification,testing,evidence,quality,edge-case,regression,acceptance,quality-gate,risk-classification"
BOT4_SKILLS="test-design,edge-case-analysis,counterexample-search,evidence-review,quality-gate,regression-risk-review,acceptance-validation,log-evidence-analysis,coverage-gap-analysis,release-readiness-check"
BOT4_SCOPES="production"

# Bot 5: Customer
BOT5_ID="客服"
BOT5_NAME="客服"
BOT5_PROFILE="customer-service"
BOT5_PORT="${BOT5_PORT:-30041}"
BOT5_PROFILE_SOURCE="customer-service"
BOT5_SUMMARY="客服，客户服务 Bot，负责接住用户现场问题和情绪，整理诉求、影响范围、复现线索和承诺风险，把用户反馈转成产品、研发、验证可处理的输入，并推动服务补救和回访闭环"
BOT5_DOMAINS="customer-service,feedback,support,service-recovery,user-voice,incident-intake,complaint-handling,followup,escalation"
BOT5_SKILLS="customer-intake,issue-triage,service-recovery,feedback-synthesis,user-voice-summarization,impact-assessment,commitment-tracking,escalation,followup-planning,external-response-drafting"
BOT5_SCOPES="production"

# Fixed gateway tokens for each bot
BOT1_GATEWAY_TOKEN="ceo_test_token"
BOT2_GATEWAY_TOKEN="product_manager_test_token"
BOT3_GATEWAY_TOKEN="engineering_test_token"
BOT4_GATEWAY_TOKEN="verification_test_token"
BOT5_GATEWAY_TOKEN="customer_service_test_token"

BOT_PROFILE_FILES=(
    "SOUL.md"
    "AGENTS.md"
    "IDENTITY.md"
    "USER.md"
    "TOOLS.md"
    "HEARTBEAT.md"
    "MEMORY.md"
    "BOOTSTRAP.md"
    "OKR.md"
    "OUTPUT.md"
    "RULES.md"
    "SAFETY.md"
    "KNOWLEDGE.md"
)

BCS_PID=""
BOT1_PID=""
BOT2_PID=""
BOT3_PID=""
BOT4_PID=""
BOT5_PID=""

# ============================================================================
# Colors
# ============================================================================

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    YELLOW='\033[1;33m'
    CYAN='\033[0;36m'
    NC='\033[0m'
else
    GREEN=''
    RED=''
    YELLOW=''
    CYAN=''
    NC=''
fi

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "  ${CYAN}→${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }

singlebox_mode_option() {
    if [ "${SINGLEBOX_MODE:-local}" = "standalone" ]; then
        echo "--standalone"
    else
        echo "--local"
    fi
}

singlebox_cmd() {
    local action="$1"
    local target="$2"
    echo "./scripts/singlebox.sh $(singlebox_mode_option) ${action} ${target}"
}

health_ready() {
    local port="$1"
    curl --noproxy '*' --connect-timeout 1 --max-time 2 -s "http://127.0.0.1:${port}/health" > /dev/null 2>&1
}

port_is_occupied() {
    [ -n "$(port_pids "$1")" ]
}

port_pids() {
    lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

port_already_assigned() {
    case " ${_ASSIGNED_BOT_PORTS:-} " in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}

config_has_bcs_core_tools() {
    local config_file="$1"
    [ -f "$config_file" ] || return 1
    for tool in bcs_route bcs_assign_task bcs_send_task_message bcs_task_complete; do
        if ! grep -q "\"${tool}\"" "$config_file"; then
            return 1
        fi
    done
}

bot_config_base_matches_local() {
    local bot_id="$1"
    local profile="$2"
    local port="$3"
    local profile_source="${4:-$profile}"
    local profile_dir
    local workspace_dir
    local config_file

    profile_dir="$(profile_dir_for "$profile")"
    workspace_dir="$(workspace_dir_for "$bot_id" "$profile" "$profile_source")"
    config_file="$profile_dir/openclaw.json"

    [ -d "$profile_dir" ] || return 1
    [ -d "$workspace_dir" ] || return 1
    [ -f "$config_file" ] || return 1

    jq -e \
        --arg bcs_url "$BCS_URL" \
        --arg bot_id "$bot_id" \
        --arg workspace "$workspace_dir" \
        --argjson port "$port" \
        '
          .channels.bcs.enabled == true
          and .channels.bcs.bcsUrl == $bcs_url
          and .agents.defaults.workspace == $workspace
          and .gateway.port == $port
          and .gateway.mode == "local"
        ' "$config_file" >/dev/null 2>&1
}

bot_config_plugin_matches_local() {
    local profile="$1"
    local config_file

    config_file="$(profile_dir_for "$profile")/openclaw.json"

    jq -e \
        --arg plugin_path "$BCN_PLUGIN_LOAD_DIR" \
        '
          ((.plugins.load.paths // []) | index($plugin_path) != null)
        ' "$config_file" >/dev/null 2>&1
}

bot_config_identity_matches_local() {
    local profile="$1"
    local config_file
    local session_bot_uuid

    config_file="$(profile_dir_for "$profile")/openclaw.json"
    session_bot_uuid="$(session_bot_uuid_for "$profile")"

    jq -e \
        --arg session_bot_uuid "$session_bot_uuid" \
        '
          if $session_bot_uuid == "" then
            (.channels.bcs | has("botId") | not)
            or .channels.bcs.botId == null
            or .channels.bcs.botId == ""
          else
            .channels.bcs.botId == $session_bot_uuid
          end
        ' "$config_file" >/dev/null 2>&1
}

bot_config_matches_local() {
    local bot_id="$1"
    local profile="$2"
    local port="$3"
    local profile_source="${4:-$profile}"

    bot_config_base_matches_local "$bot_id" "$profile" "$port" "$profile_source" || return 1
    bot_config_plugin_matches_local "$profile" || return 1
    bot_config_identity_matches_local "$profile"
}

bot_runtime_matches_local() {
    local bot_id="$1"
    local profile="$2"
    local port="$3"
    local profile_source="${4:-$profile}"

    health_ready "$port" || return 1
    bot_config_matches_local "$bot_id" "$profile" "$port" "$profile_source"
}

load_bot_ports() {
    [ -f "$BCS_BOT_PORTS_FILE" ] || return 0

    local key value
    while IFS='=' read -r key value; do
        case "$key" in
            BOT1_PORT|BOT2_PORT|BOT3_PORT|BOT4_PORT|BOT5_PORT)
                case "$value" in
                    ''|*[!0-9]*) ;;
                    *) eval "$key=$value" ;;
                esac
                ;;
        esac
    done < "$BCS_BOT_PORTS_FILE"
}

save_bot_ports() {
    mkdir -p "$(dirname "$BCS_BOT_PORTS_FILE")"
    {
        echo "BOT1_PORT=$BOT1_PORT"
        echo "BOT2_PORT=$BOT2_PORT"
        echo "BOT3_PORT=$BOT3_PORT"
        echo "BOT4_PORT=$BOT4_PORT"
        echo "BOT5_PORT=$BOT5_PORT"
    } > "$BCS_BOT_PORTS_FILE"
}

assign_bot_ports() {
    [ "$BCS_BOT_PORT_AUTO" = "1" ] || return 0

    _ASSIGNED_BOT_PORTS=""
    local var label bot_id profile profile_source preferred port
    for spec in \
        "BOT1_PORT|$BOT1_ID|$BOT1_ID|$BOT1_PROFILE|$BOT1_PROFILE_SOURCE" \
        "BOT2_PORT|$BOT2_ID|$BOT2_ID|$BOT2_PROFILE|$BOT2_PROFILE_SOURCE" \
        "BOT3_PORT|$BOT3_ID|$BOT3_ID|$BOT3_PROFILE|$BOT3_PROFILE_SOURCE" \
        "BOT4_PORT|$BOT4_ID|$BOT4_ID|$BOT4_PROFILE|$BOT4_PROFILE_SOURCE" \
        "BOT5_PORT|$BOT5_ID|$BOT5_ID|$BOT5_PROFILE|$BOT5_PROFILE_SOURCE"; do
        IFS='|' read -r var label bot_id profile profile_source <<< "$spec"
        eval "preferred=\${$var}"
        port="$preferred"

        while port_already_assigned "$port" || { port_is_occupied "$port" && ! bot_runtime_matches_local "$bot_id" "$profile" "$port" "$profile_source"; }; do
            port=$((port + 1))
        done

        if [ "$port" != "$preferred" ]; then
            warn "$label port $preferred is in use; using $port because BCS_BOT_PORT_AUTO=1"
        elif port_is_occupied "$port"; then
            info "$label port $port already has matching local OpenClaw runtime; keeping it"
        fi

        eval "$var=$port"
        _ASSIGNED_BOT_PORTS="${_ASSIGNED_BOT_PORTS} ${port}"
    done

    save_bot_ports
}

process_cwd() {
    local pid="$1"
    lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1
}

process_command() {
    local pid="$1"
    ps -p "$pid" -o command= 2>/dev/null || true
}

path_is_under_dir() {
    local path="$1"
    local dir="$2"
    case "$path" in
        "$dir"|"$dir"/*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

describe_process() {
    local pid="$1"
    local cwd
    local command
    cwd="$(process_cwd "$pid")"
    command="$(process_command "$pid")"
    echo "PID ${pid}, cwd=${cwd:-unknown}, command=${command:-unknown}"
}

terminate_process() {
    local pid="$1"
    local label="$2"
    local waited=0

    if ! kill -0 "$pid" 2>/dev/null; then
        return 0
    fi

    info "Stopping $label ($(describe_process "$pid"))"
    kill "$pid" 2>/dev/null || true
    while [ "$waited" -lt 5 ]; do
        if ! kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done

    if kill -0 "$pid" 2>/dev/null; then
        warn "$label did not exit after SIGTERM; force killing PID $pid"
        kill -9 "$pid" 2>/dev/null || true
    fi
}

stop_pid_if_owned() {
    local pid="$1"
    local label="$2"
    local cwd

    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
        return 0
    fi

    cwd="$(process_cwd "$pid")"
    if [ -z "$cwd" ]; then
        warn "Skipping $label PID $pid: cannot verify process cwd"
        return 1
    fi

    if path_is_under_dir "$cwd" "$REPO_ROOT"; then
        terminate_process "$pid" "$label"
        return 0
    fi

    warn "Skipping $label PID $pid: process is outside current checkout (cwd=$cwd)"
    return 1
}

stop_port_if_owned() {
    local port="$1"
    local label="$2"
    local pids
    local pid

    pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
    for pid in $pids; do
        stop_pid_if_owned "$pid" "$label on port $port" || true
    done
}

build_env_openclaw_models_json() {
    local provider_id="${OPENCLAW_OPENAI_PROVIDER_ID:-openai-compatible}"
    local model_name="${OPENCLAW_OPENAI_MODEL_NAME:-$OPENCLAW_OPENAI_MODEL_ID}"
    local model_api="${OPENCLAW_OPENAI_MODEL_API:-openai-completions}"

    jq -cn \
        --arg provider_id "$provider_id" \
        --arg base_url "$OPENCLAW_OPENAI_BASE_URL" \
        --arg api_key "$OPENCLAW_OPENAI_API_KEY" \
        --arg model_id "$OPENCLAW_OPENAI_MODEL_ID" \
        --arg model_name "$model_name" \
        --arg model_api "$model_api" \
        '{
          mode: "merge",
          providers: {
            ($provider_id): {
              baseUrl: $base_url,
              apiKey: $api_key,
              auth: "api-key",
              api: $model_api,
              models: [
                {
                  id: $model_id,
                  name: $model_name,
                  api: $model_api,
                  input: ["text"]
                }
              ]
            }
          }
        }'
}

build_env_openclaw_agent_model_fields_json() {
    local provider_id="${OPENCLAW_OPENAI_PROVIDER_ID:-openai-compatible}"
    local model_name="${OPENCLAW_OPENAI_MODEL_NAME:-$OPENCLAW_OPENAI_MODEL_ID}"

    jq -cn \
        --arg provider_id "$provider_id" \
        --arg model_id "$OPENCLAW_OPENAI_MODEL_ID" \
        --arg model_name "$model_name" \
        '{
      model: {
        primary: ($provider_id + "/" + $model_id)
      },
      models: {
        ($provider_id + "/" + $model_id): {
          alias: $model_name
        }
      }
    }'
}

load_openclaw_model_config_source() {
    local source="$1"

    if ! jq -e . "$source" >/dev/null 2>&1; then
        warn "OpenClaw model config source is not valid JSON, ignoring: $source"
        return 1
    fi

    if ! jq -e '(.models? != null) or (.agents.defaults.model? != null) or (.agents.defaults.models? != null) or (.agents.defaults.imageModel? != null)' "$source" >/dev/null; then
        warn "OpenClaw config source has no model fields, ignoring: $source"
        return 1
    fi

    OPENCLAW_MODELS_JSON="$(jq -c '.models // empty' "$source")"
    OPENCLAW_AGENT_MODEL_FIELDS_JSON="$(
        jq -c '
          {}
          + (if .agents.defaults.model? != null then {model: .agents.defaults.model} else {} end)
          + (if .agents.defaults.models? != null then {models: .agents.defaults.models} else {} end)
          + (if .agents.defaults.imageModel? != null then {imageModel: .agents.defaults.imageModel} else {} end)
        ' "$source"
    )"
    return 0
}

confirm_openclaw_model_config_reuse() {
    local source="$1"
    local answer

    if [ -t 0 ] && [ -t 1 ]; then
        printf "Reuse OpenClaw model config from %s for 5bot profiles? [Y/n] " "$source" >/dev/tty
        read -r answer </dev/tty || answer=""
        case "$answer" in
            n|N|no|No|NO)
                warn "Skipped OpenClaw model config source: $source"
                return 1
                ;;
        esac
        info "Reusing OpenClaw model config after user confirmation: $source"
        return 0
    fi

    info "Non-interactive mode; reusing OpenClaw model config by default: $source"
    return 0
}

prepare_openclaw_model_config() {
    local source="${OPENCLAW_MODEL_CONFIG_SOURCE}"

    if [ -n "${OPENCLAW_OPENAI_BASE_URL:-}" ] || [ -n "${OPENCLAW_OPENAI_API_KEY:-}" ] || [ -n "${OPENCLAW_OPENAI_MODEL_ID:-}" ]; then
        if [ -z "${OPENCLAW_OPENAI_BASE_URL:-}" ] || [ -z "${OPENCLAW_OPENAI_API_KEY:-}" ] || [ -z "${OPENCLAW_OPENAI_MODEL_ID:-}" ]; then
            warn "Incomplete OpenAI-compatible model env config; ignoring OPENCLAW_OPENAI_*."
            warn "Set OPENCLAW_OPENAI_BASE_URL, OPENCLAW_OPENAI_API_KEY, and OPENCLAW_OPENAI_MODEL_ID together."
        else
            OPENCLAW_MODELS_JSON="$(build_env_openclaw_models_json)"
            OPENCLAW_AGENT_MODEL_FIELDS_JSON="$(build_env_openclaw_agent_model_fields_json)"
            info "Using OPENCLAW_OPENAI_BASE_URL / OPENCLAW_OPENAI_API_KEY / OPENCLAW_OPENAI_MODEL_ID for 5bot model config."
            return 0
        fi
    fi

    if [ -f "$source" ] && load_openclaw_model_config_source "$source"; then
        if confirm_openclaw_model_config_reuse "$source"; then
            info "Only model-related fields are copied into isolated 5bot profiles; the source config is unchanged."
            return 0
        fi
    fi

    OPENCLAW_MODELS_JSON=""
    OPENCLAW_AGENT_MODEL_FIELDS_JSON="{}"
    warn "No OpenClaw model config found for 5bot profiles."
    warn "Set OPENCLAW_MODEL_CONFIG_SOURCE=$HOME/.openclaw/openclaw.json or OPENCLAW_OPENAI_BASE_URL / OPENCLAW_OPENAI_API_KEY / OPENCLAW_OPENAI_MODEL_ID to enable real model replies."
    warn "BCS, plugin connection, session creation, and onboard can still be verified without model credentials."
}

build_agent_defaults_json() {
    local workspace_dir="$1"

    jq -cn \
        --arg workspace "$workspace_dir" \
        --argjson model_fields "$OPENCLAW_AGENT_MODEL_FIELDS_JSON" \
        '$model_fields + {
          workspace: $workspace,
          compaction: {
            mode: "safeguard"
          },
          maxConcurrent: 4,
          subagents: {
            maxConcurrent: 8
          }
        }'
}

effective_server_env() {
    echo "${SERVER_ENV:-${BCS_SERVER_ENV:-local}}"
}

should_onboard_after_start() {
    case "${RUN_ONBOARD_AFTER_START:-auto}" in
        1|true|yes|on)
            return 0
            ;;
        0|false|no|off)
            return 1
            ;;
        auto|"")
            return 0
            ;;
        *)
            fail "Invalid RUN_ONBOARD_AFTER_START: ${RUN_ONBOARD_AFTER_START}"
            fail "Valid values: auto, 1, 0"
            exit 1
            ;;
    esac
}

# ============================================================================
# Cleanup
# ============================================================================

cleanup() {
    echo ""
    info "Cleaning up..."
    [ -n "$BOT1_PID" ] && kill "$BOT1_PID" 2>/dev/null || true
    [ -n "$BOT2_PID" ] && kill "$BOT2_PID" 2>/dev/null || true
    [ -n "$BOT3_PID" ] && kill "$BOT3_PID" 2>/dev/null || true
    [ -n "$BOT4_PID" ] && kill "$BOT4_PID" 2>/dev/null || true
    [ -n "$BOT5_PID" ] && kill "$BOT5_PID" 2>/dev/null || true
    [ -n "$BCS_PID" ] && kill "$BCS_PID" 2>/dev/null || true
    sleep 1
    [ -n "$BOT1_PID" ] && kill -9 "$BOT1_PID" 2>/dev/null || true
    [ -n "$BOT2_PID" ] && kill -9 "$BOT2_PID" 2>/dev/null || true
    [ -n "$BOT3_PID" ] && kill -9 "$BOT3_PID" 2>/dev/null || true
    [ -n "$BOT4_PID" ] && kill -9 "$BOT4_PID" 2>/dev/null || true
    [ -n "$BOT5_PID" ] && kill -9 "$BOT5_PID" 2>/dev/null || true
    [ -n "$BCS_PID" ] && kill -9 "$BCS_PID" 2>/dev/null || true
}

# ============================================================================
# Copy business skills to bot workspace
# ============================================================================

copy_business_skill() {
    local bot_skills_dir="$1"
    local skill_name="$2"

    # Try to find skill in project skills directory
    local skill_src=""

    # Exact match
    if [ -d "$PROJECT_ROOT/skills/$skill_name" ]; then
        skill_src="$PROJECT_ROOT/skills/$skill_name"
    else
        # Try to find by prefix match (e.g., "pmo-summary(3)" for "pmo-summary")
        for dir in "$PROJECT_ROOT/skills"/*/; do
            local dirname=$(basename "$dir")
            if [[ "$dirname" == "$skill_name"* ]]; then
                skill_src="$dir"
                break
            fi
        done
    fi

    if [ -n "$skill_src" ] && [ -d "$skill_src" ]; then
        local dest_dir="$bot_skills_dir/$skill_name"
        mkdir -p "$dest_dir"
        cp -r "$skill_src"/* "$dest_dir/"
        pass "Copied skill: $skill_name"
    else
        warn "Skill not found: $skill_name (will create placeholder)"
        # Create placeholder SKILL.md
        local placeholder_dir="$bot_skills_dir/$skill_name"
        mkdir -p "$placeholder_dir"
        cat > "$placeholder_dir/SKILL.md" << EOF
---
name: $skill_name
description: "Skill placeholder - TODO: implement"
---

# $skill_name

**Status**: Placeholder - needs implementation

This skill is registered in BCS capabilities but the SKILL.md file is not yet implemented.
EOF
    fi
}

copy_bot_profile_files() {
    local profile_source="$1"
    local workspace_dir="$2"
    local source_dir="$FIVE_BOTS_PROFILE_DIR/$profile_source"
    local file

    if [ ! -d "$source_dir" ]; then
        fail "5bot profile source not found: $source_dir"
        return 1
    fi

    for file in "${BOT_PROFILE_FILES[@]}"; do
        if [ ! -f "$source_dir/$file" ]; then
            fail "5bot profile file missing: $source_dir/$file"
            return 1
        fi
    done

    for file in "${BOT_PROFILE_FILES[@]}"; do
        cp "$source_dir/$file" "$workspace_dir/$file"
    done

    pass "Copied 5bot profile: $profile_source -> $workspace_dir"
}

setup_bcs_coordination_skill() {
    local workspace_dir="$1"
    local skills_dir="$workspace_dir/skills"
    local skill_source_dir="$PROJECT_ROOT/crates/tools/bcs-cli/bcs-coordination"

    if [ ! -d "$skill_source_dir" ]; then
        fail "bcs-coordination skill not found: $skill_source_dir"
        return 1
    fi

    mkdir -p "$skills_dir"
    rm -rf "${skills_dir}/bcs-coordination"
    cp -R "$skill_source_dir" "$skills_dir/" || return 1
}

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
    local business_skills="${12:-}"  # Comma-separated list of business skill names
    local profile_source="${13:-$profile}"

    local profile_dir
    profile_dir="$(profile_dir_for "$profile")"
    local workspace_dir
    workspace_dir="$(workspace_dir_for "$bot_id" "$profile" "$profile_source")"
    local skills_dir="$workspace_dir/skills"

    mkdir -p "$profile_dir" "$workspace_dir" "$LOG_DIR" "$skills_dir"
    copy_bot_profile_files "$profile_source" "$workspace_dir"
    setup_bcs_coordination_skill "$workspace_dir" || return 1

    local config_file="$profile_dir/openclaw.json"
    if [ "$BCS_BOTS_PRESERVE_FILES" = "1" ] && [ -f "$config_file" ]; then
        if config_has_bcs_core_tools "$config_file" && bot_config_matches_local "$bot_id" "$profile" "$port" "$profile_source"; then
            info "Preserving existing profile directory: $profile ($bot_id)"
            return 0
        fi
        info "Refreshing existing profile config with BCS tool allowlist or session bot identity: $profile ($bot_id)"
    fi

    # Copy business skills if specified
    if [ -n "$business_skills" ]; then
        IFS=',' read -ra skill_array <<< "$business_skills"
        for skill_name in "${skill_array[@]}"; do
            skill_name=$(echo "$skill_name" | xargs)  # Trim whitespace
            if [ -n "$skill_name" ]; then
                copy_business_skill "$skills_dir" "$skill_name"
            fi
        done
    fi

    local models_block=""
    if [ -n "$OPENCLAW_MODELS_JSON" ]; then
        models_block="  \"models\": $OPENCLAW_MODELS_JSON,"
    fi
    local agent_defaults_json
    agent_defaults_json="$(build_agent_defaults_json "$workspace_dir")"
    local session_bot_uuid
    local bot_id_config_line=""
    session_bot_uuid="$(session_bot_uuid_for "$profile")"
    if [ -n "$session_bot_uuid" ]; then
        bot_id_config_line="      \"botId\": $(jq -cn --arg bot_id "$session_bot_uuid" '$bot_id'),"
    fi

    # Create openclaw.json config
    cat > "$profile_dir/openclaw.json" << EOF
{
  "meta": {
    "lastTouchedVersion": "2026.3.12"
  },
${models_block}
  "agents": {
    "defaults": $agent_defaults_json,
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
    "profile": "coding",
    "alsoAllow": [
      "bcs_route",
      "bcs_assign_task",
      "bcs_send_task_message",
      "bcs_task_complete"
    ]
  },
  "messages": {
    "ackReactionScope": "group-mentions",
    "groupChat": {
      "visibleReplies": "automatic"
    }
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
${bot_id_config_line}
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
    }
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
        "$BCN_PLUGIN_LOAD_DIR"
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

    # Copy provider keys if exists
    if [ -f "$HOME/.config/moltis/provider_keys.json" ]; then
        mkdir -p "$profile_dir/config"
        cp "$HOME/.config/moltis/provider_keys.json" "$profile_dir/config/" 2>/dev/null || true
    fi

    info "Profile directory setup complete: $profile ($bot_id)"
}

# ============================================================================
# Start BCS Server
# ============================================================================

start_bcs() {
    # Note: All info/pass/warn/fail output goes to stderr (>&2).
    info "Starting BCS server on port $BCS_PORT..." >&2

    # Check if BCS binary exists
    if [ ! -f "$BCS_BIN" ]; then
        fail "BCS binary not found at $BCS_BIN. Run 'cargo build' first." >&2
        return 1
    fi

    # Kill any existing BCS process on the port
    local existing_pid
    existing_pid="$(port_pids "$BCS_PORT")"
    if [ -n "$existing_pid" ]; then
        stop_port_if_owned "$BCS_PORT" "existing BCS" >&2
        if port_is_occupied "$BCS_PORT"; then
            fail "BCS port $BCS_PORT is in use by a process outside this checkout. Stop it manually or use a different BCS_PORT." >&2
            return 1
        fi
    fi

    # Start BCS.
    # Default to local so this script uses file-backed SQLite (no remote deps).
    # Set SERVER_ENV=dev to use the remote storage/cache-backed setup.
    export SERVER_ENV="${SERVER_ENV:-local}"
    export RUST_LOG="${RUST_LOG:-info}"
    export BCS_DATA_DIR="${BCS_DATA_DIR:-$BOTS_BASE_DIR/data}"
    mkdir -p "$BCS_DATA_DIR"
    info "SERVER_ENV=$SERVER_ENV" >&2
    info "BCS_CONFIG_DIR=$BCS_CONFIG_DIR" >&2

    # ------------------------------------------------------------------------
    # Local Auth Mock (debug builds only) — see bcs-config-local.toml header.
    #
    # When `BCS_AUTH_MOCK=1` is set in the parent shell, inject a synthetic
    # BuserviceLoginUser into BCS so `auth::extract_user_identity` returns a
    # valid (staff_no, nick_name) pair without needing a real Buservice cookie.
    # This is the local-test path for onboard-time human-actor auto-registration.
    #
    # All three BCS_MOCK_* fall back to sensible defaults so a single
    # `export BCS_AUTH_MOCK=1` is enough; you can still override any of them
    # in the parent shell to test multi-user / different-channel scenarios.
    # ------------------------------------------------------------------------
    if [ "${BCS_AUTH_MOCK:-0}" = "1" ]; then
        export BCS_AUTH_MOCK=1
        export BCS_MOCK_USER_ID="${BCS_MOCK_USER_ID:-001}"
        export BCS_MOCK_USER_NICK_NAME="${BCS_MOCK_USER_NICK_NAME:-admin}"
        export BCS_MOCK_USER_CHANNEL="${BCS_MOCK_USER_CHANNEL:-mock}"
        warn "BCS_AUTH_MOCK enabled: user_id=$BCS_MOCK_USER_ID nick_name=$BCS_MOCK_USER_NICK_NAME channel=$BCS_MOCK_USER_CHANNEL" >&2
    fi

    if [ "$BCS_BOTS_DETACHED" = "1" ]; then
        nohup "$BCS_BIN" --config-dir "$BCS_CONFIG_DIR" > "$BCS_LOG" 2>&1 < /dev/null &
    else
        "$BCS_BIN" --config-dir "$BCS_CONFIG_DIR" &> "$BCS_LOG" &
    fi
    local pid=$!

    # Wait for BCS to be ready
    for i in $(seq 1 30); do
        if health_ready "$BCS_PORT"; then
            BCS_PID="$pid"
            pass "BCS server started on port $BCS_PORT (PID $pid)" >&2
            return 0
        fi
        sleep 1
    done

    fail "BCS server failed to start (check $BCS_LOG)" >&2
    return 1
}

ensure_existing_bcs() {
    if health_ready "$BCS_PORT"; then
        pass "Using existing BCS server on port $BCS_PORT"
        return 0
    fi

    fail "BCS is not running on port $BCS_PORT. Start BCS first, or use '$0 start'."
    return 1
}

# ============================================================================
# Start OpenClaw Gateway
# ============================================================================

start_openclaw() {
    local bot_id="$1"
    local profile="$2"
    local port="$3"
    local log_file="$4"
    local profile_source="${5:-$profile}"

    # Note: All info/pass/warn/fail output goes to stderr (>&2) because
    # this function's stdout is captured by the caller via $(start_openclaw ...).
    # Only the PID is echoed to stdout.
    info "Starting OpenClaw ($bot_id) on port $port with profile $profile..." >&2

    local profile_dir
    profile_dir="$(profile_dir_for "$profile")"
    local workspace_dir
    workspace_dir="$(workspace_dir_for "$bot_id" "$profile" "$profile_source")"
    local existing_pids
    existing_pids="$(port_pids "$port")"
    local bcs_cli_dir="$PROJECT_ROOT/target/debug"

    if [ -n "$existing_pids" ]; then
        if bot_runtime_matches_local "$bot_id" "$profile" "$port" "$profile_source"; then
            pass "OpenClaw ($bot_id) already running on port $port with matching local profile/workspace; reusing it" >&2
            START_OPENCLAW_PID="$(echo "$existing_pids" | head -1)"
            echo "$START_OPENCLAW_PID"
            return 0
        fi

        fail "OpenClaw ($bot_id) port $port is in use by a non-matching process (PID: $(echo "$existing_pids" | tr '\n' ' ' | xargs))" >&2
        echo ""
        return 1
    fi

    # Run OpenClaw gateway in background.
    # Set explicit OpenClaw paths so each bot stores runtime state in its own profile dir.
    if [ "$BCS_BOTS_DETACHED" = "1" ]; then
        NODE_TLS_REJECT_UNAUTHORIZED=0 \
        BCS_IGNORE_CREDENTIALS=1 \
        OPENCLAW_GATEWAY_TOKEN="" \
        PATH="$bcs_cli_dir:$PATH" \
        BOT_DATA_DIR="$profile_dir" \
        BCS_API_BASE_URL="$BCS_API_BASE_URL" \
        OPENCLAW_DATA_DIR="$profile_dir" \
        OPENCLAW_STATE_DIR="$profile_dir" \
        OPENCLAW_CONFIG_PATH="$profile_dir/openclaw.json" \
        OPENCLAW_WORKSPACE_DIR="$workspace_dir" \
        nohup openclaw --profile "$profile" gateway run --port "$port" > "$log_file" 2>&1 < /dev/null &
    else
        NODE_TLS_REJECT_UNAUTHORIZED=0 \
        BCS_IGNORE_CREDENTIALS=1 \
        OPENCLAW_GATEWAY_TOKEN="" \
        PATH="$bcs_cli_dir:$PATH" \
        BOT_DATA_DIR="$profile_dir" \
        BCS_API_BASE_URL="$BCS_API_BASE_URL" \
        OPENCLAW_DATA_DIR="$profile_dir" \
        OPENCLAW_STATE_DIR="$profile_dir" \
        OPENCLAW_CONFIG_PATH="$profile_dir/openclaw.json" \
        OPENCLAW_WORKSPACE_DIR="$workspace_dir" \
        openclaw --profile "$profile" gateway run --port "$port" &> "$log_file" &
    fi
    local pid=$!

    # Wait for gateway to be ready
    for i in $(seq 1 30); do
        if health_ready "$port"; then
            START_OPENCLAW_PID="$pid"
            pass "OpenClaw ($bot_id) started on port $port (PID $pid)" >&2
            echo "$pid"
            return 0
        fi
        sleep 1
    done

    fail "OpenClaw ($bot_id) failed to start (check $log_file)" >&2
    echo ""
    return 1
}

# ============================================================================
# Stop All
# ============================================================================

stop_all() {
    local force="${1:-false}"

    info "Stopping BCS and all OpenClaw bots..."

    if [ "$force" = "true" ]; then
        # Kill by port
        for port in $BCS_PORT $BOT1_PORT $BOT2_PORT $BOT3_PORT $BOT4_PORT $BOT5_PORT; do
            local pids
            pids="$(port_pids "$port")"
            if [ -n "$pids" ]; then
                info "Force stopping processes on port $port: $pids"
                echo "$pids" | xargs kill 2>/dev/null || true
                sleep 1
                echo "$pids" | xargs kill -9 2>/dev/null || true
            fi
        done

        info "Force stopping all openclaw gateway processes..."
        pkill -f "openclaw.*gateway" 2>/dev/null || true
        sleep 1
        pkill -9 -f "openclaw.*gateway" 2>/dev/null || true
        # Also kill bcs
        pkill -f "target/debug/bcs" 2>/dev/null || true
        pkill -f "target/release/bcs" 2>/dev/null || true
    else
        for port in $BCS_PORT $BOT1_PORT $BOT2_PORT $BOT3_PORT $BOT4_PORT $BOT5_PORT; do
            stop_port_if_owned "$port" "local BCS stack process"
        done
    fi

    pass "All processes stopped"
}

stop_bots() {
    local force="${1:-false}"

    info "Stopping OpenClaw bots..."

    local port
    for port in $BOT1_PORT $BOT2_PORT $BOT3_PORT $BOT4_PORT $BOT5_PORT; do
        if [ "$force" = "true" ]; then
            local pids
            pids="$(port_pids "$port")"
            if [ -n "$pids" ]; then
                info "Force stopping OpenClaw bot process(es) on port $port: $pids"
                echo "$pids" | xargs kill 2>/dev/null || true
                sleep 1
                echo "$pids" | xargs kill -9 2>/dev/null || true
            fi
        else
            stop_port_if_owned "$port" "OpenClaw bot"
        fi
    done

    pass "OpenClaw bots stopped"
}

# ============================================================================
# Show Status
# ============================================================================

show_status() {
    echo ""
    info "Service Status:"

    # BCS Status
    if health_ready "$BCS_PORT"; then
        pass "BCS (port $BCS_PORT): running"
    else
        warn "BCS (port $BCS_PORT): not running"
    fi

    # Bot Status
    for bot_info in "$BOT1_ID:$BOT1_PORT:$BOT1_PROFILE" \
                    "$BOT2_ID:$BOT2_PORT:$BOT2_PROFILE" \
                    "$BOT3_ID:$BOT3_PORT:$BOT3_PROFILE" \
                    "$BOT4_ID:$BOT4_PORT:$BOT4_PROFILE" \
                    "$BOT5_ID:$BOT5_PORT:$BOT5_PROFILE"; do
        local bot_id="${bot_info%%:*}"
        local rest="${bot_info#*:}"
        local port="${rest%%:*}"
        local profile="${rest#*:}"
        local session_bot_uuid
        session_bot_uuid="$(session_bot_uuid_for "$profile")"
        if health_ready "$port"; then
            pass "$bot_id (port $port, profile $profile, bot_uuid ${session_bot_uuid:-unassigned}): running"
        else
            warn "$bot_id (port $port, profile $profile, bot_uuid ${session_bot_uuid:-unassigned}): not running"
        fi
    done
    echo ""
}

# ============================================================================
# Get Bot Token
# ============================================================================

get_bot_token() {
    local profile="$1"
    local session_file
    session_file="$(bcs_session_file_for "$profile")"
    [ -f "$session_file" ] || return 0
    jq -r 'if (.token | type) == "string" then .token else empty end' "$session_file" 2>/dev/null | head -n 1
}

# ============================================================================
# Bot Onboarding
# ============================================================================

cmd_onboard() {
    info "Completing bot onboarding..."

    # Wait for bots to connect and get tokens
    info "Waiting for bots to connect to BCS..."
    sleep 5

    # Run a single bot's onboard + visibility set, capturing all output to a
    # temp file so parallel runs don't interleave.  Returns 0 on success.
    onboard_bot_quiet() {
        local bot_id="$1"
        local profile="$2"
        local summary="$3"
        local domains="$4"
        local skills="$5"
        local scopes="$6"
        local log_file="$7"

        {
            local token=""
            local onboard_attempt
            local onboard_output
            for onboard_attempt in 1 2; do
                token=""
                for i in $(seq 1 30); do
                    token=$(get_bot_token "$profile")
                    if [ -n "$token" ]; then
                        break
                    fi
                    if [ "$i" = "1" ]; then
                        warn "Token for $bot_id not found, waiting..."
                    fi
                    sleep 2
                done

                if [ -z "$token" ]; then
                    fail "Cannot find a fresh token for $bot_id. Make sure bot is connected to BCS."
                    return 1
                fi

                info "Onboarding $bot_id..."
                if onboard_output="$(
                    BOT_DATA_DIR="$(profile_dir_for "$profile")" \
                        "$BCS_CLI" onboard \
                            --token "$token" \
                            --name "$bot_id" \
                            --summary "$summary" \
                            --domains "$domains" \
                            --skills "$skills" \
                            --scopes "$scopes" 2>&1
                )"; then
                    printf '%s\n' "$onboard_output"
                    break
                fi

                printf '%s\n' "$onboard_output"
                if printf '%s\n' "$onboard_output" | grep -Eq 'valid bot token is required|401 Unauthorized'; then
                    fail "Token for $bot_id was rejected by BCS; refusing to clear session during onboard"
                    fail "Run $(singlebox_cmd clean bots) or $(singlebox_cmd clean bcs_bots) if you intend to reset bot identity"
                    return 1
                fi

                fail "Failed to onboard $bot_id"
                return 1
            done

            info "Setting visibility=public for $bot_id..."
            BOT_DATA_DIR="$(profile_dir_for "$profile")" \
                "$BCS_CLI" visibility set --value public || {
                fail "Failed to set visibility for $bot_id"
                return 1
            }
            pass "$bot_id onboarded & set public!"
        } > "$log_file" 2>&1
    }

    # Onboard bots in parallel — each one waits for its own token
    # independently and buffers output to a temp file so it won't
    # interleave with other bots.
    local onboard_pids=()
    local onboard_logs=()
    local bot_configs=(
        "$BOT1_ID:$BOT1_PROFILE:$BOT1_SUMMARY:$BOT1_DOMAINS:$BOT1_SKILLS:$BOT1_SCOPES"
        "$BOT2_ID:$BOT2_PROFILE:$BOT2_SUMMARY:$BOT2_DOMAINS:$BOT2_SKILLS:$BOT2_SCOPES"
        "$BOT3_ID:$BOT3_PROFILE:$BOT3_SUMMARY:$BOT3_DOMAINS:$BOT3_SKILLS:$BOT3_SCOPES"
        "$BOT4_ID:$BOT4_PROFILE:$BOT4_SUMMARY:$BOT4_DOMAINS:$BOT4_SKILLS:$BOT4_SCOPES"
        "$BOT5_ID:$BOT5_PROFILE:$BOT5_SUMMARY:$BOT5_DOMAINS:$BOT5_SKILLS:$BOT5_SCOPES"
    )
    for bc in "${bot_configs[@]}"; do
        IFS=':' read -r id profile summary domains skills scopes <<< "$bc"
        local _log=$(mktemp)
        onboard_logs+=("$_log")
        onboard_bot_quiet "$id" "$profile" "$summary" "$domains" "$skills" "$scopes" "$_log" &
        onboard_pids+=($!)
    done

    # Print a single-line progress indicator while onboarding runs.
    info "Onboarding 5 bots in parallel..."
    local finished=0
    for pid in "${onboard_pids[@]}"; do
        wait "$pid" && finished=$((finished + 1)) || true
    done

    local failed=$(( ${#onboard_pids[@]} - finished ))

    # Replay each bot's buffered output sequentially (clean, no interleaving).
    for log in "${onboard_logs[@]}"; do
        cat "$log"
        rm -f "$log"
    done

    if [ "$failed" -gt 0 ]; then
        fail "$failed bot(s) failed to onboard"
        return 1
    fi

    pass "All bots onboarded!"
}

# ============================================================================
# Clean Profile Directories
# ============================================================================

clean_profiles() {
    info "Cleaning profile directories..."
    local bot_specs=(
        "$BOT1_ID:$BOT1_PROFILE:$BOT1_PROFILE_SOURCE"
        "$BOT2_ID:$BOT2_PROFILE:$BOT2_PROFILE_SOURCE"
        "$BOT3_ID:$BOT3_PROFILE:$BOT3_PROFILE_SOURCE"
        "$BOT4_ID:$BOT4_PROFILE:$BOT4_PROFILE_SOURCE"
        "$BOT5_ID:$BOT5_PROFILE:$BOT5_PROFILE_SOURCE"
    )
    local spec bot_id profile profile_source rest
    for spec in "${bot_specs[@]}"; do
        bot_id="${spec%%:*}"
        rest="${spec#*:}"
        profile="${rest%%:*}"
        profile_source="${rest#*:}"
        rm -rf "$(profile_dir_for "$profile")" 2>/dev/null || true
        rm -rf "$(workspace_dir_for "$bot_id" "$profile" "$profile_source")" 2>/dev/null || true
    done
    pass "Profile directories cleaned"
}

# ============================================================================
# Link BCN Plugin
# ============================================================================

bcn_plugin_needs_build() {
    local plugin_load_dir="$1"
    local dist_file="$plugin_load_dir/dist/esm/index.js"

    if [ ! -f "$dist_file" ]; then
        return 0
    fi
    if [ ! -d "$plugin_load_dir/node_modules" ] && [ ! -d "$BCN_PLUGIN_SRC_DIR/node_modules" ]; then
        return 0
    fi
    if [ -d "$BCN_PLUGIN_SRC_DIR/src" ]; then
        local newer_source
        newer_source=$(find "$BCN_PLUGIN_SRC_DIR/src" -type f -name '*.ts' -newer "$dist_file" -print -quit)
        if [ -n "$newer_source" ]; then
            return 0
        fi
    fi
    return 1
}

link_bcn_plugin() {
    local project_bcn_path="$BCN_PLUGIN_LOAD_DIR"

    # Ensure openclaw.plugin.json exists at plugin root (not just in dist/)
    # OpenClaw infers plugin id from the entry file's parent directory name.
    # Without a root-level manifest, it sees "dist/index.js" and guesses id="dist".
    if [ ! -f "$project_bcn_path/openclaw.plugin.json" ] && [ -f "$project_bcn_path/dist/openclaw.plugin.json" ]; then
        cp "$project_bcn_path/dist/openclaw.plugin.json" "$project_bcn_path/openclaw.plugin.json"
        pass "Copied openclaw.plugin.json to plugin root"
    fi

    # Ensure package.json exists at plugin root to declare the entry point
    if [ ! -f "$project_bcn_path/package.json" ]; then
        cat > "$project_bcn_path/package.json" << 'PKGJSON'
{
  "name": "@avernet-plugin/openclaw-channel-bcn",
  "version": "0.1.0",
  "main": "dist/index.js",
  "private": true
}
PKGJSON
        pass "Created package.json at plugin root"
    fi

    # Build BCN plugin when outputs are missing or TypeScript sources are newer.
    if [ "$BCN_PLUGIN_SOURCE" != "npm" ] && [ -d "$BCN_PLUGIN_SRC_DIR" ]; then
        if bcn_plugin_needs_build "$project_bcn_path"; then
            info "Building BCN plugin..."
            local npm_cmd="npm"
            command -v "$npm_cmd" >/dev/null 2>&1 || fail "npm was not found; install Node.js with npm before building the BCN plugin"
            if (cd "$BCN_PLUGIN_SRC_DIR" && "$npm_cmd" install && "$npm_cmd" run build); then
                # Touch dist so its mtime is newer than all source files,
                # preventing repeated rebuilds when output content is unchanged.
                touch "$project_bcn_path/dist/esm/index.js" 2>/dev/null || true
                pass "BCN plugin built"
            else
                fail "Failed to build BCN plugin"
            fi
        else
            pass "BCN plugin already built, skipping"
        fi
    fi

    # Link BCN plugin to user extensions directory
    local user_ext_dir="$OPENCLAW_EXTENSIONS_ROOT"
    local user_bcn_link="$user_ext_dir/openclaw-channel-bcn"

    if [ "$project_bcn_path" = "$user_bcn_link" ]; then
        pass "BCN plugin already at extensions path: $user_bcn_link"
        return 0
    fi

    if [ ! -d "$user_ext_dir" ]; then
        mkdir -p "$user_ext_dir"
    fi

    if [ -L "$user_bcn_link" ]; then
        local current_target=$(readlink "$user_bcn_link")
        if [ "$current_target" = "$project_bcn_path" ]; then
            pass "BCN plugin already linked at $user_bcn_link"
        elif [ "$OPENCLAW_EXTENSIONS_REPLACE_LINKS" = "1" ]; then
            rm -f "$user_bcn_link"
            ln -s "$project_bcn_path" "$user_bcn_link"
            pass "BCN plugin relinked: $user_bcn_link -> $project_bcn_path"
        else
            warn "BCN plugin link points elsewhere, keeping: $user_bcn_link -> $current_target"
            warn "Current run still loads plugin directly from: $project_bcn_path"
        fi
    elif [ -d "$user_bcn_link" ]; then
        if [ "$OPENCLAW_EXTENSIONS_REPLACE_LINKS" = "1" ]; then
            fail "BCN plugin path exists as a directory: $user_bcn_link"
        else
            warn "BCN plugin path exists as a directory, keeping: $user_bcn_link"
            warn "Current run still loads plugin directly from: $project_bcn_path"
        fi
    else
        ln -s "$project_bcn_path" "$user_bcn_link"
        pass "BCN plugin linked: $user_bcn_link -> $project_bcn_path"
    fi
}

# ============================================================================
# Check Prerequisites
# ============================================================================

check_prerequisites() {
    info "Checking prerequisites..."

    if [ "${START_BOTS_ONLY:-0}" != "1" ]; then
        # Check BCS binary
        if [ ! -f "$BCS_BIN" ]; then
            fail "BCS binary not found: $BCS_BIN"
            info "Run: cargo build --package bcs"
            return 1
        fi
        pass "BCS binary found"
    fi

    # Check BCS CLI
    if [ ! -f "$BCS_CLI" ]; then
        fail "BCS CLI not found: $BCS_CLI"
        info "Run: cargo build --package bcs-cli"
        return 1
    fi
    pass "BCS CLI found"

    if [ "${START_BOTS_ONLY:-0}" != "1" ]; then
        # Check BCS Admin
        if [ ! -f "$BCS_ADMIN" ]; then
            fail "BCS Admin not found: $BCS_ADMIN"
            info "Run: cargo build --package bcs-admin"
            return 1
        fi
        pass "BCS Admin found"
    fi

    # Check openclaw command
    if ! command -v openclaw &> /dev/null; then
        fail "openclaw command not found"
        info "Install openclaw first"
        return 1
    fi
    pass "openclaw command found"

    # jq is used to safely copy only model-related JSON fields from the user's
    # local OpenClaw config into isolated 5bot profiles.
    if ! command -v jq &> /dev/null; then
        fail "jq command not found"
        info "Install jq before running the 5bot stack"
        return 1
    fi
    pass "jq command found"

    # Check BCN plugin
    if [ ! -d "$BCN_PLUGIN_SRC_DIR" ]; then
        fail "BCN plugin source not found: $BCN_PLUGIN_SRC_DIR"
        return 1
    fi
    if [ ! -f "$BCN_PLUGIN_LOAD_DIR/openclaw.plugin.json" ]; then
        fail "BCN plugin manifest not found: $BCN_PLUGIN_LOAD_DIR/openclaw.plugin.json"
        return 1
    fi
    if [ ! -f "$BCN_PLUGIN_LOAD_DIR/dist/esm/index.js" ]; then
        warn "BCN plugin build output not found, will build in setup phase"
    else
        pass "BCN plugin build output found"
    fi
    pass "BCN plugin found: $BCN_PLUGIN_LOAD_DIR"

    local occupied=false
    for bot_info in "$BOT1_ID:$BOT1_PROFILE:$BOT1_PORT:$BOT1_PROFILE_SOURCE" \
                    "$BOT2_ID:$BOT2_PROFILE:$BOT2_PORT:$BOT2_PROFILE_SOURCE" \
                    "$BOT3_ID:$BOT3_PROFILE:$BOT3_PORT:$BOT3_PROFILE_SOURCE" \
                    "$BOT4_ID:$BOT4_PROFILE:$BOT4_PORT:$BOT4_PROFILE_SOURCE" \
                    "$BOT5_ID:$BOT5_PROFILE:$BOT5_PORT:$BOT5_PROFILE_SOURCE"; do
        local bot_id="${bot_info%%:*}"
        local rest="${bot_info#*:}"
        local profile
        profile="${rest%%:*}"
        rest="${rest#*:}"
        local port="${rest%%:*}"
        local profile_source="${rest#*:}"
        local pids
        pids="$(port_pids "$port")"
        if [ -n "$pids" ]; then
            if bot_runtime_matches_local "$bot_id" "$profile" "$port" "$profile_source"; then
                pass "$bot_id port $port already has matching local OpenClaw runtime; will reuse it"
            else
                fail "$bot_id port $port is already in use by a non-matching process (PID: $(echo "$pids" | tr '\n' ' ' | xargs))"
                occupied=true
            fi
        fi
    done
    if [ "$occupied" = true ]; then
        info "Only existing OpenClaw bot processes whose port, profile, workspace, BCS URL, and plugin path match this local stack are reused."
        info "Stop the unrelated process, or set BCS_BOT_PORT_AUTO=1 to choose available bot gateway ports automatically."
        return 1
    fi

    local profile_mismatch=false
    for bot_info in "$BOT1_ID:$BOT1_PROFILE:$BOT1_PORT:$BOT1_PROFILE_SOURCE" \
                    "$BOT2_ID:$BOT2_PROFILE:$BOT2_PORT:$BOT2_PROFILE_SOURCE" \
                    "$BOT3_ID:$BOT3_PROFILE:$BOT3_PORT:$BOT3_PROFILE_SOURCE" \
                    "$BOT4_ID:$BOT4_PROFILE:$BOT4_PORT:$BOT4_PROFILE_SOURCE" \
                    "$BOT5_ID:$BOT5_PROFILE:$BOT5_PORT:$BOT5_PROFILE_SOURCE"; do
        local bot_id="${bot_info%%:*}"
        local rest="${bot_info#*:}"
        local profile
        profile="${rest%%:*}"
        rest="${rest#*:}"
        local port="${rest%%:*}"
        local profile_source="${rest#*:}"
        local profile_dir
        profile_dir="$(profile_dir_for "$profile")"
        if [ -f "$profile_dir/openclaw.json" ] && ! bot_config_matches_local "$bot_id" "$profile" "$port" "$profile_source"; then
            if bot_config_base_matches_local "$bot_id" "$profile" "$port" "$profile_source"; then
                info "$bot_id profile will be refreshed with current BCN plugin path or BCS session bot identity: $profile_dir"
            else
                fail "$bot_id profile exists but does not match this singlebox local stack: $profile_dir"
                info "Expected port=$port, BCS URL=$BCS_URL, workspace=$(workspace_dir_for "$bot_id" "$profile" "$profile_source")"
                info "Use matching local settings, clean the owning stack after confirming ownership, or choose an isolated standalone root."
                profile_mismatch=true
            fi
        fi
    done
    if [ "$profile_mismatch" = true ]; then
        return 1
    fi

    return 0
}

# ============================================================================
# Main
# ============================================================================

case "${1:-start}" in
    start)
        if [ "$BCS_BOTS_DETACHED" != "1" ]; then
            trap cleanup EXIT INT TERM
        fi

        if [ "$BCS_BOT_PORT_AUTO" = "1" ]; then
            load_bot_ports
            assign_bot_ports
        fi

        # Check prerequisites first
        check_prerequisites || exit 1

        info "Setting up environment..."

        # Link BCN plugin to system extensions
        link_bcn_plugin

        if [ "${START_BOTS_ONLY:-0}" = "1" ] && [ "$(effective_server_env)" = "dev" ] && [ "$BCS_BOTS_PRESERVE_FILES" != "1" ]; then
            BCS_BOTS_PRESERVE_FILES=1
            info "SERVER_ENV=dev start-bots preserves existing bot profile/session files"
        fi

        if [ "$BCS_BOTS_PRESERVE_FILES" = "1" ]; then
            info "Preserving existing bot profile and workspace files"
        else
            clean_profiles
        fi
        mkdir -p "$LOG_DIR"

        prepare_openclaw_model_config

        # Setup Bot 1: CEO
        setup_profile_dir "$BOT1_ID" "$BOT1_PROFILE" "$BOT1_PORT" \
            "$BOT1_SUMMARY" "$BOT1_DOMAINS" "$BOT1_SKILLS" "$BOT1_SCOPES" \
            "" \
            "" \
            "" \
            "$BOT1_GATEWAY_TOKEN" \
            "" \
            "$BOT1_PROFILE_SOURCE"

        # Setup Bot 2: Product
        setup_profile_dir "$BOT2_ID" "$BOT2_PROFILE" "$BOT2_PORT" \
            "$BOT2_SUMMARY" "$BOT2_DOMAINS" "$BOT2_SKILLS" "$BOT2_SCOPES" \
            "" \
            "" \
            "" \
            "$BOT2_GATEWAY_TOKEN" \
            "" \
            "$BOT2_PROFILE_SOURCE"

        # Setup Bot 3: Engineering
        setup_profile_dir "$BOT3_ID" "$BOT3_PROFILE" "$BOT3_PORT" \
            "$BOT3_SUMMARY" "$BOT3_DOMAINS" "$BOT3_SKILLS" "$BOT3_SCOPES" \
            "" \
            "" \
            "" \
            "$BOT3_GATEWAY_TOKEN" \
            "" \
            "$BOT3_PROFILE_SOURCE"

        # Setup Bot 4: Verification
        setup_profile_dir "$BOT4_ID" "$BOT4_PROFILE" "$BOT4_PORT" \
            "$BOT4_SUMMARY" "$BOT4_DOMAINS" "$BOT4_SKILLS" "$BOT4_SCOPES" \
            "" \
            "" \
            "" \
            "$BOT4_GATEWAY_TOKEN" \
            "" \
            "$BOT4_PROFILE_SOURCE"

        # Setup Bot 5: Customer
        setup_profile_dir "$BOT5_ID" "$BOT5_PROFILE" "$BOT5_PORT" \
            "$BOT5_SUMMARY" "$BOT5_DOMAINS" "$BOT5_SKILLS" "$BOT5_SCOPES" \
            "" \
            "" \
            "" \
            "$BOT5_GATEWAY_TOKEN" \
            "" \
            "$BOT5_PROFILE_SOURCE"

        if [ "${START_BOTS_ONLY:-0}" = "1" ]; then
            ensure_existing_bcs || exit 1
        else
            echo ""
            info "Starting BCS server..."
            start_bcs || exit 1
        fi

        echo ""
        if [ "$BCS_BOTS_DETACHED" = "1" ]; then
            info "Starting OpenClaw bots..."
        else
            info "Starting OpenClaw bots (parallel)..."
        fi

        bot_start_specs=(
            "$BOT1_ID:$BOT1_PROFILE:$BOT1_PORT:$BOT1_PROFILE_SOURCE"
            "$BOT2_ID:$BOT2_PROFILE:$BOT2_PORT:$BOT2_PROFILE_SOURCE"
            "$BOT3_ID:$BOT3_PROFILE:$BOT3_PORT:$BOT3_PROFILE_SOURCE"
            "$BOT4_ID:$BOT4_PROFILE:$BOT4_PORT:$BOT4_PROFILE_SOURCE"
            "$BOT5_ID:$BOT5_PROFILE:$BOT5_PORT:$BOT5_PROFILE_SOURCE"
        )

        if [ "$BCS_BOTS_DETACHED" = "1" ]; then
            _bot_pids=()
            for spec in "${bot_start_specs[@]}"; do
                IFS=':' read -r bot_id profile port profile_source <<< "$spec"
                start_openclaw "$bot_id" "$profile" "$port" "$LOG_DIR/${bot_id}.log" "$profile_source" > /dev/null || exit 1
                _bot_pids+=("$START_OPENCLAW_PID")
            done

            BOT1_PID="${_bot_pids[0]}"
            BOT2_PID="${_bot_pids[1]}"
            BOT3_PID="${_bot_pids[2]}"
            BOT4_PID="${_bot_pids[3]}"
            BOT5_PID="${_bot_pids[4]}"
            unset _bot_pids
        else
            # Start all bots in parallel; each start_openclaw waits for its own
            # health check before returning, so they all progress concurrently.
            # We use temp files to capture the PID each sub-shell echoes to stdout
            # (since $(...) would serialize the calls).
            _bot_start_pids=()
            _bot_pid_files=()
            for spec in "${bot_start_specs[@]}"; do
                IFS=':' read -r bot_id profile port profile_source <<< "$spec"
                _pid_file=$(mktemp)
                _bot_pid_files+=("$_pid_file")
                start_openclaw "$bot_id" "$profile" "$port" "$LOG_DIR/${bot_id}.log" "$profile_source" > "$_pid_file" &
                _bot_start_pids+=($!)
            done

            _start_failed=0
            for i in "${!_bot_start_pids[@]}"; do
                if ! wait "${_bot_start_pids[$i]}"; then
                    _start_failed=$((_start_failed + 1))
                fi
            done

            if [ "$_start_failed" -gt 0 ]; then
                fail "$_start_failed bot(s) failed to start"
                exit 1
            fi

            # Read captured PIDs into the global variables (for cleanup)
            BOT1_PID=$(cat "${_bot_pid_files[0]}" 2>/dev/null | tr -d '[:space:]')
            BOT2_PID=$(cat "${_bot_pid_files[1]}" 2>/dev/null | tr -d '[:space:]')
            BOT3_PID=$(cat "${_bot_pid_files[2]}" 2>/dev/null | tr -d '[:space:]')
            BOT4_PID=$(cat "${_bot_pid_files[3]}" 2>/dev/null | tr -d '[:space:]')
            BOT5_PID=$(cat "${_bot_pid_files[4]}" 2>/dev/null | tr -d '[:space:]')
            rm -f "${_bot_pid_files[@]}"
            unset _bot_start_pids _bot_pid_files _start_failed _pid_file
        fi
        unset bot_start_specs

        echo ""
        if should_onboard_after_start; then
            cmd_onboard
        fi

        if [ "${START_BOTS_ONLY:-0}" = "1" ]; then
            pass "All bots started successfully!"
        else
            pass "All services started successfully!"
        fi
        echo ""
        info "=== 服务信息 ==="
        info "BCS Server: http://localhost:$BCS_PORT"
        info "BCS WebSocket: $BCS_URL"
        echo ""
        info "=== BOT 端口 ==="
        info "$BOT1_ID: port $BOT1_PORT"
        info "$BOT2_ID: port $BOT2_PORT"
        info "$BOT3_ID: port $BOT3_PORT"
        info "$BOT4_ID: port $BOT4_PORT"
        info "$BOT5_ID: port $BOT5_PORT"
        echo ""
        info "Logs: $LOG_DIR/"
        info "Press Ctrl+C to stop all services"

        if [ "$BCS_BOTS_DETACHED" = "1" ]; then
            info "Detached mode enabled; leaving services running"
            exit 0
        fi

        # Wait forever (can't use bare `wait` because background processes were
        # started inside subshells via $(...), so they aren't children of this shell)
        tail -f /dev/null &
        TAIL_PID=$!
        wait $TAIL_PID
        ;;

    start-bots)
        export START_BOTS_ONLY=1
        export RUN_ONBOARD_AFTER_START="${RUN_ONBOARD_AFTER_START:-auto}"
        exec bash "$SCRIPT_DIR/start_bcs_bots.sh" start
        ;;

    start-bots-onboard|start-bots+onboard)
        export START_BOTS_ONLY=1
        export RUN_ONBOARD_AFTER_START=1
        exec bash "$SCRIPT_DIR/start_bcs_bots.sh" start
        ;;

    stop)
        load_bot_ports
        stop_all false
        ;;

    stop-bots)
        load_bot_ports
        stop_bots false
        ;;

    force-stop|clean)
        load_bot_ports
        stop_all true
        clean_profiles
        ;;

    force-stop-bots|clean-bots)
        load_bot_ports
        stop_bots true
        clean_profiles
        ;;

    status)
        load_bot_ports
        show_status
        ;;

    onboard)
        load_bot_ports
        cmd_onboard
        ;;

    build)
        info "Building BCS, BCS CLI, and BCS Admin..."
        (cd "$PROJECT_ROOT" && cargo build --package bcs --package bcs-cli --package bcs-admin)
        pass "Build complete"
        ;;

    test-friend)
        BCS_PORT="$BCS_PORT" bash "$SCRIPT_DIR/test_friend.sh" "${2:-all}"
        ;;

    test-visibility)
        BCS_PORT="$BCS_PORT" bash "$SCRIPT_DIR/test_visibility.sh" "${2:-all}"
        ;;

    test-human-group|test-5bot-human-group)
        BCS_PORT="$BCS_PORT" bash "$SCRIPT_DIR/test_5bot_human_group.sh" "${2:-all}"
        ;;

    *)
        echo "Usage: $0 {start|start-bots|start-bots-onboard|stop|force-stop|clean|status|onboard|build|test-friend|test-visibility|test-human-group}"
        echo ""
        echo "Commands:"
        echo "  start            - Setup and start BCS + all 5 OpenClaw bots"
        echo "  start-bots       - Setup/start 5 bots; SERVER_ENV=local onboards, SERVER_ENV=dev preserves profile/session and skips onboard"
        echo "  start-bots-onboard - Compatibility alias for start-bots"
        echo "  stop             - Stop all services by port"
        echo "  force-stop       - Force stop ALL processes and clean profiles"
        echo "  stop-bots        - Stop only the 5 OpenClaw bot gateways"
        echo "  clean-bots       - Stop bot gateways and clean bot profiles/workspaces"
        echo "  clean            - Alias for force-stop"
        echo "  status           - Show service status"
        echo "  onboard          - Complete bot onboarding with BCS"
        echo "  build            - Build BCS, BCS CLI, and BCS Admin binaries"
        echo "  test-friend      - Test friend request flow (requires BCS running)"
        echo "  test-visibility  - Test visibility management (requires BCS running)"
        echo "  test-human-group - Onboard, friend, create 5-bot group, and self-join HUMAN_USER_ID"
        exit 1
        ;;
esac
