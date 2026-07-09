#!/usr/bin/env bash
# Start a local mixed BCS topology:
#
#   OpenClaw gateway bot  -> BCS /ws/bot                 (ordinary WS bot)
#   Claude Code provider  -> BCS Provider downlink API    (Provider bot)
#                            -> mock_provider_bridge.py
#                            -> engine adapter (claude_code)
#                            -> teamclaw-aicoding-relay
#
# Usage:
#   scripts/run_bcs_mixed_provider.sh start [options]
#   scripts/run_bcs_mixed_provider.sh stop
#   scripts/run_bcs_mixed_provider.sh status
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENGINE_DIR="${PROJECT_ROOT}/src/engine"
BCS_DIR="${PROJECT_ROOT}/src/bcs"
BCS_CLI="${BCS_DIR}/target/debug/bcs-cli"
BRIDGE="${PROJECT_ROOT}/src/bcs/scripts/mock_provider_bridge.py"
SINGLEBOX="${SCRIPT_DIR}/singlebox.sh"
DEP_DIR="${SCRIPT_DIR}/.dependences"
LOG_DIR="${DEP_DIR}/logs"
STATE_DIR="${DEP_DIR}/mixed_provider"

RELAY_DIR="${RELAY_DIR:-/Users/ray/ant/projects/teamclaw-aicoding-relay}"
FRONTEND_DIR="${FRONTEND_DIR:-/Users/ray/ant/projects/open-claw}"

BCS_PORT="${BCS_PORT:-21000}"
OPENCLAW_PORT="${OPENCLAW_PORT:-30101}"
RELAY_PORT="${RELAY_PORT:-18900}"
ENGINE_PORT="${ENGINE_PORT:-20003}"
MOCK_PORT="${MOCK_PORT:-28080}"
FRONTEND_PORT="${FRONTEND_PORT:-8000}"

STAFF_NO="${BCS_MOCK_USER_STAFF_NO:-410025}"
STAFF_NAME="${BCS_MOCK_USER_NICK_NAME:-${STAFF_NO}}"
OPENCLAW_PROFILE="${OPENCLAW_PROFILE:-bcs_mixed_openclaw}"
OPENCLAW_BOT_ID="${OPENCLAW_BOT_ID:-openclaw-mixed-local}"
OPENCLAW_BOT_NAME="${OPENCLAW_BOT_NAME:-OpenClaw Mixed Local}"
OPENCLAW_SUMMARY="${OPENCLAW_SUMMARY:-OpenClaw local bot connected through BCS WebSocket}"
OPENCLAW_DOMAINS="${OPENCLAW_DOMAINS:-openclaw,local,collaboration}"
OPENCLAW_SKILLS="${OPENCLAW_SKILLS:-chat,collaboration,bcs_route}"
OPENCLAW_SCOPES="${OPENCLAW_SCOPES:-local}"

PROVIDER_NAME="${PROVIDER_NAME:-mock-claude-code-provider}"
PROVIDER_BOT_REF="${PROVIDER_BOT_REF:-claude-code-provider-local}"
PROVIDER_BOT_NAME="${PROVIDER_BOT_NAME:-Claude Code Provider Local}"
PROVIDER_BOT_SUMMARY="${PROVIDER_BOT_SUMMARY:-Claude Code bot exposed through BCS Provider downlink}"
PROVIDER_BOT_DOMAINS="${PROVIDER_BOT_DOMAINS:-coding,claude_code,provider}"
PROVIDER_BOT_SKILLS="${PROVIDER_BOT_SKILLS:-code_review,code_generation,debugging}"
PROVIDER_BOT_SCOPES="${PROVIDER_BOT_SCOPES:-local}"
PROVIDER_COORDINATION_MODE="${PROVIDER_COORDINATION_MODE:-mcporter_mcp}"
PROVIDER_COORDINATION_MCP_SERVER="${PROVIDER_COORDINATION_MCP_SERVER:-bcs}"
PROVIDER_COORDINATION_MCPORTER_COMMAND="${PROVIDER_COORDINATION_MCPORTER_COMMAND:-mcporter}"
PROVIDER_PERMISSION_MODE="${PROVIDER_PERMISSION_MODE:-bypassPermissions}"

RELAY_DEFAULT_MODEL="${RELAY_DEFAULT_MODEL:-claude-sonnet-4-5}"
CLAUDE_CODE_DEFAULT_CWD="${CLAUDE_CODE_DEFAULT_CWD:-${HOME}/.claude_code/workspace}"
FRONTEND_DEV_SCRIPT="${FRONTEND_DEV_SCRIPT:-devs:local}"
MOCK_MODE="sse"
START_FRONTEND="1"
DRY_RUN="0"
CMD="start"
OPENCLAW_SESSION_MARKER="0"

OPENCLAW_PID_FILE="${STATE_DIR}/openclaw_bot.pid"
RELAY_PID_FILE="${STATE_DIR}/relay.pid"
ENGINE_PID_FILE="${STATE_DIR}/engine.pid"
MOCK_PID_FILE="${STATE_DIR}/mock_provider.pid"
FRONTEND_PID_FILE="${STATE_DIR}/frontend.pid"
OPENCLAW_LOG="${LOG_DIR}/mixed_openclaw_bot.log"
RELAY_LOG="${LOG_DIR}/mixed_relay.log"
ENGINE_LOG="${LOG_DIR}/mixed_engine_claude_code.log"
MOCK_LOG="${LOG_DIR}/mixed_mock_provider.log"
FRONTEND_LOG="${LOG_DIR}/mixed_frontend.log"

usage() {
  cat <<EOF
OpenClaw WS bot + Claude Code Provider bot mixed BCS launcher.

Usage:
  $0 [start|stop|status] [options]

Options:
  --mode sse|callback-2.0|callback-1.0   Provider response mode (default: sse)
  --relay-dir PATH                       teamclaw-aicoding-relay repo
                                         default: /Users/ray/ant/projects/teamclaw-aicoding-relay
  --frontend-dir PATH                    open-claw frontend repo
                                         default: /Users/ray/ant/projects/open-claw
  --bcs-port PORT                        BCS port (default: 21000)
  --openclaw-port PORT                   OpenClaw WS bot gateway port (default: 30101)
  --relay-port PORT                      Claude Code relay port (default: 18900)
  --engine-port PORT                     Engine adapter port (default: 20003)
  --mock-port PORT                       Provider webhook bridge port (default: 28080)
  --staff-no STAFF_NO                    Mock BCS user staff number (default: 410025)
  --no-frontend                          Do not start frontend
  --dry-run                              Print actions without starting/stopping services
  -h, --help                             Show this help

The Claude Code side is registered as a BCS Provider bot. This script does not
start relay's direct BCS bridge (tnpm run dev:bcs).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    start|stop|status) CMD="$1"; shift ;;
    --mode) MOCK_MODE="$2"; shift 2 ;;
    --relay-dir) RELAY_DIR="$2"; shift 2 ;;
    --frontend-dir) FRONTEND_DIR="$2"; shift 2 ;;
    --bcs-port) BCS_PORT="$2"; shift 2 ;;
    --openclaw-port) OPENCLAW_PORT="$2"; shift 2 ;;
    --relay-port) RELAY_PORT="$2"; shift 2 ;;
    --engine-port) ENGINE_PORT="$2"; shift 2 ;;
    --mock-port) MOCK_PORT="$2"; shift 2 ;;
    --staff-no) STAFF_NO="$2"; STAFF_NAME="${BCS_MOCK_USER_NICK_NAME:-$2}"; shift 2 ;;
    --no-frontend) START_FRONTEND="0"; shift ;;
    --frontend) START_FRONTEND="1"; shift ;;
    --dry-run) DRY_RUN="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'unknown arg: %s\n' "$1" >&2; usage >&2; exit 1 ;;
  esac
done

case "${MOCK_MODE}" in
  sse|callback-2.0) REG_PROTOCOL_VERSION="2.0" ;;
  callback-1.0) REG_PROTOCOL_VERSION="1.0" ;;
  *) printf 'unknown --mode: %s (want: sse | callback-2.0 | callback-1.0)\n' "${MOCK_MODE}" >&2; exit 1 ;;
esac

mkdir -p "${LOG_DIR}" "${STATE_DIR}"

log() { printf '\033[1;34m[mixed-provider]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[mixed-provider]\033[0m %s\n' "$*" >&2; }
die() { err "$*"; exit 1; }
api() { curl --noproxy '*' -fsS "$@"; }

dry() {
  if [[ "${DRY_RUN}" == "1" ]]; then
    printf 'DRY RUN: %s\n' "$*"
    return 0
  fi
  return 1
}

require_file() {
  local file="$1" label="$2"
  [[ "${DRY_RUN}" == "1" || -f "${file}" ]] || die "${label} not found: ${file}"
}

require_dir() {
  local dir="$1" label="$2"
  [[ "${DRY_RUN}" == "1" || -d "${dir}" ]] || die "${label} not found: ${dir}"
}

preflight_setup_visibility() {
  local ext_dir="${HOME}/.openclaw/extensions"
  local plugin_src="${PROJECT_ROOT}/src/plugin/packages/openclaw-channel-bcn"

  [[ "${DRY_RUN}" == "1" ]] && return 0

  if [[ ! -x "${BCS_CLI}" || ! -x "${BCS_DIR}/target/debug/bcs-admin" ]]; then
    log "BCS binaries are incomplete; setup will run cargo build for bcs/bcs-cli/bcs-admin"
  fi

  if [[ -d "${plugin_src}" ]] && { [[ ! -d "${plugin_src}/node_modules" ]] || [[ ! -f "${plugin_src}/dist/esm/index.js" ]]; }; then
    log "BCN plugin is not built; setup will run tnpm install && tnpm run build in ${plugin_src}"
  fi

  mkdir -p "${ext_dir}" 2>/dev/null || true
  local probe="${ext_dir}/.mixed_provider_write_probe_$$"
  if ! touch "${probe}" 2>/dev/null; then
    die "${ext_dir} is not writable. Fix it first with: sudo chown -R \$(whoami) ${HOME}/.openclaw"
  fi
  rm -f "${probe}"
}

port_pids() {
  lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

kill_port() {
  local port="$1" pids
  if dry "free port ${port}"; then return 0; fi
  pids="$(port_pids "${port}")"
  if [[ -n "${pids}" ]]; then
    log "freeing port ${port} (pid: ${pids})"
    kill ${pids} 2>/dev/null || true
    sleep 1
    pids="$(port_pids "${port}")"
    [[ -n "${pids}" ]] && kill -9 ${pids} 2>/dev/null || true
  fi
}

kill_pid_file() {
  local pid_file="$1" label="$2" pid
  if dry "stop ${label} using ${pid_file}"; then return 0; fi
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    log "stopping ${label} (pid ${pid})"
    kill "${pid}" 2>/dev/null || true
    sleep 1
    kill -0 "${pid}" 2>/dev/null && kill -9 "${pid}" 2>/dev/null || true
  fi
  rm -f "${pid_file}"
}

wait_port() {
  local port="$1" label="$2" max="${3:-60}"
  for _ in $(seq 1 "${max}"); do
    if lsof -tiTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
      log "${label} listening on ${port}"
      return 0
    fi
    sleep 0.5
  done
  die "${label} did not listen on ${port}"
}

wait_http() {
  local url="$1" label="$2" max="${3:-60}"
  for _ in $(seq 1 "${max}"); do
    if api "${url}" >/dev/null 2>&1; then
      log "${label} healthy: ${url}"
      return 0
    fi
    sleep 0.5
  done
  die "${label} not healthy: ${url}"
}

json_get() {
  local key="$1"
  python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "${key}"
}

json_get_file() {
  local file="$1" key="$2"
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "${file}" "${key}"
}

file_mtime_epoch() {
  local file="$1"
  if stat -f %m "${file}" >/dev/null 2>&1; then
    stat -f %m "${file}"
  else
    stat -c %Y "${file}"
  fi
}

load_openclaw_session() {
  local session_file="$1" min_mtime="${2:-0}" mtime
  SESSION_TOKEN=""
  SESSION_BOT_UUID=""
  SESSION_MTIME="0"

  [[ -f "${session_file}" ]] || return 1
  mtime="$(file_mtime_epoch "${session_file}" 2>/dev/null || printf '0')"
  [[ "${mtime}" -ge "${min_mtime}" ]] || return 1

  SESSION_TOKEN="$(json_get_file "${session_file}" token 2>/dev/null || true)"
  SESSION_BOT_UUID="$(json_get_file "${session_file}" bot_uuid 2>/dev/null || true)"
  SESSION_MTIME="${mtime}"
  [[ -n "${SESSION_TOKEN}" && -n "${SESSION_BOT_UUID}" && "${SESSION_BOT_UUID}" != "null" ]]
}

csv_json_array() {
  local csv="$1"
  python3 -c 'import json,sys; print(json.dumps([p.strip() for p in sys.argv[1].split(",") if p.strip()], ensure_ascii=False))' "${csv}"
}

setup_bcs_and_plugin() {
  log "setting up BCS binaries and BCN plugin"

  if [[ ! -x "${BCS_DIR}/target/debug/bcs" ]]; then
    if dry "cd ${BCS_DIR} && cargo build --package bcs"; then :; else
      (cd "${BCS_DIR}" && cargo build --package bcs)
    fi
  fi

  if [[ ! -x "${BCS_CLI}" ]]; then
    if dry "cd ${BCS_DIR} && cargo build --package bcs-cli"; then :; else
      (cd "${BCS_DIR}" && cargo build --package bcs-cli)
    fi
  fi

  if [[ ! -x "${BCS_DIR}/target/debug/bcs-admin" ]]; then
    if dry "cd ${BCS_DIR} && cargo build --package bcs-admin"; then :; else
      (cd "${BCS_DIR}" && cargo build --package bcs-admin)
    fi
  fi

  setup_bcn_plugin
}

setup_bcn_plugin() {
  local extensions_dir="${HOME}/.openclaw/extensions"
  local plugin_link="${extensions_dir}/openclaw-channel-bcn"
  local plugin_src="${PROJECT_ROOT}/src/plugin/packages/openclaw-channel-bcn"
  local tshy_self_link="${plugin_src}/src/node_modules/@alipay/openclaw-channel-bcn"
  local needs_build=0

  if [[ ! -d "${plugin_src}" ]]; then
    log "BCN plugin source not found, skipping: ${plugin_src}"
    return 0
  fi

  if [[ "${DRY_RUN}" == "1" ]]; then
    dry "remove stale tshy self-link ${plugin_src}/src/node_modules if present"
  elif [[ -L "${tshy_self_link}" ]]; then
    log "removing stale tshy self-link and generated BCN plugin build artifacts"
    rm -rf "${plugin_src}/src/node_modules" "${plugin_src}/.tshy" "${plugin_src}/.tshy-build" "${plugin_src}/dist"
    needs_build=1
  fi

  if [[ "${needs_build}" == "1" || ! -f "${plugin_src}/dist/esm/index.js" || ! -d "${plugin_src}/node_modules" ]]; then
    if dry "cd ${plugin_src} && tnpm install && tnpm run build"; then :; else
      log "building BCN plugin"
      (cd "${plugin_src}" && tnpm install && tnpm run build)
    fi
  else
    log "BCN plugin already built"
  fi

  if [[ "${DRY_RUN}" == "1" ]]; then
    dry "ensure symlink ${plugin_link} -> ${plugin_src}"
    return 0
  fi

  mkdir -p "${extensions_dir}"
  if [[ -L "${plugin_link}" || -d "${plugin_link}" ]]; then
    log "BCN plugin link already exists: ${plugin_link}"
    return 0
  fi
  ln -s "${plugin_src}" "${plugin_link}"
  log "BCN plugin linked: ${plugin_link} -> ${plugin_src}"
}

start_clean_bcs() {
  log "starting clean BCS on ${BCS_PORT} as staff_no=${STAFF_NO} (no 5-bot stack)"
  if dry "BCS_AUTO_ONBOARD=0 BCS_AUTH_MOCK=1 BCS_MOCK_USER_STAFF_NO=${STAFF_NO} bash ${SINGLEBOX} start bcs"; then return 0; fi
  BCS_PORT="${BCS_PORT}" BCS_AUTO_ONBOARD=0 BCS_AUTH_MOCK=1 \
    BCS_MOCK_USER_STAFF_NO="${STAFF_NO}" BCS_MOCK_USER_NICK_NAME="${STAFF_NAME}" \
    BCS_MOCK_USER_CHANNEL="${BCS_MOCK_USER_CHANNEL:-mock}" \
    bash "${SINGLEBOX}" start bcs
  wait_http "http://127.0.0.1:${BCS_PORT}/health" "BCS" 120
}

bcn_plugin_load_dir() {
  local src="${PROJECT_ROOT}/src/plugin/packages/openclaw-channel-bcn"
  local package="${src}/package"
  if [[ -f "${package}/openclaw.plugin.json" && -f "${package}/dist/esm/index.js" ]]; then
    printf '%s\n' "${package}"
  else
    printf '%s\n' "${src}"
  fi
}

write_openclaw_profile() {
  local profile_dir="${HOME}/.openclaw-${OPENCLAW_PROFILE}"
  local workspace_dir="${STATE_DIR}/openclaw-workspace"
  local skills_dir="${workspace_dir}/skills"
  local plugin_dir
  plugin_dir="$(bcn_plugin_load_dir)"

  if dry "write OpenClaw profile ${profile_dir} with BCS_URL=ws://127.0.0.1:${BCS_PORT}/ws/bot"; then return 0; fi

  mkdir -p "${profile_dir}" "${workspace_dir}" "${skills_dir}"
  if [[ -d "${BCS_DIR}/crates/bcs-cli/bcs-coordination" ]]; then
    rm -rf "${skills_dir}/bcs-coordination"
    cp -R "${BCS_DIR}/crates/bcs-cli/bcs-coordination" "${skills_dir}/"
  fi

  cat > "${workspace_dir}/SOUL.md" <<EOF
You are ${OPENCLAW_BOT_NAME}, a local OpenClaw participant in a BCS mixed-mode test group.
EOF
  cat > "${workspace_dir}/RULES.md" <<EOF
- Respond concisely when mentioned.
- Use bcs_route when explicitly routing work to another BCS participant.
EOF
  cat > "${workspace_dir}/MEMORY.md" <<EOF
This workspace is generated by run_bcs_mixed_provider.sh.
EOF

  cat > "${profile_dir}/openclaw.json" <<EOF
{
  "meta": { "lastTouchedVersion": "2026.3.12" },
  "agents": {
    "defaults": {
      "workspace": "${workspace_dir}",
      "compaction": { "mode": "safeguard" },
      "maxConcurrent": 4,
      "subagents": { "maxConcurrent": 8 }
    },
    "list": [{ "id": "main" }]
  },
  "skills": { "allowBundled": [] },
  "tools": {
    "profile": "coding",
    "alsoAllow": ["bcs_route"]
  },
  "messages": {
    "ackReactionScope": "group-mentions",
    "groupChat": { "visibleReplies": "automatic" }
  },
  "commands": {
    "native": "auto",
    "nativeSkills": "auto",
    "restart": true,
    "ownerDisplay": "raw"
  },
  "session": { "dmScope": "per-channel-peer" },
  "channels": {
    "bcs": {
      "enabled": true,
      "bcsUrl": "ws://127.0.0.1:${BCS_PORT}/ws/bot",
      "botId": "${OPENCLAW_BOT_ID}",
      "botName": "${OPENCLAW_BOT_NAME}",
      "capabilities": {
        "summary": "${OPENCLAW_SUMMARY}",
        "domains": $(csv_json_array "${OPENCLAW_DOMAINS}"),
        "skills": $(csv_json_array "${OPENCLAW_SKILLS}"),
        "scopes": $(csv_json_array "${OPENCLAW_SCOPES}")
      },
      "heartbeatIntervalMs": 60000,
      "reconnectIntervalMs": 5000,
      "connectionTimeoutMs": 30000
    }
  },
  "gateway": {
    "port": ${OPENCLAW_PORT},
    "mode": "local",
    "bind": "loopback",
    "controlUi": { "dangerouslyDisableDeviceAuth": true },
    "auth": { "mode": "none" },
    "tailscale": { "mode": "off", "resetOnExit": false }
  },
  "plugins": {
    "load": { "paths": ["${plugin_dir}"] },
    "entries": { "openclaw-channel-bcn": { "enabled": true } }
  }
}
EOF
}

start_openclaw_bot() {
  local profile_dir="${HOME}/.openclaw-${OPENCLAW_PROFILE}"
  local session_file="${profile_dir}/.bcs/session.json"

  log "starting OpenClaw WS bot ${OPENCLAW_BOT_ID} on ${OPENCLAW_PORT}"
  write_openclaw_profile
  if dry "remove stale ${session_file} && OPENCLAW_DATA_DIR=${profile_dir} openclaw --profile ${OPENCLAW_PROFILE} gateway run --port ${OPENCLAW_PORT}"; then return 0; fi
  kill_port "${OPENCLAW_PORT}"
  OPENCLAW_SESSION_MARKER="$(date +%s)"
  if [[ -f "${session_file}" ]]; then
    log "removing stale OpenClaw BCS session: ${session_file}"
    rm -f "${session_file}"
  fi
  NODE_TLS_REJECT_UNAUTHORIZED=0 BCS_IGNORE_CREDENTIALS=1 OPENCLAW_GATEWAY_TOKEN="" \
    OPENCLAW_DATA_DIR="${profile_dir}" \
    nohup openclaw --profile "${OPENCLAW_PROFILE}" gateway run --port "${OPENCLAW_PORT}" \
    >> "${OPENCLAW_LOG}" 2>&1 &
  echo $! > "${OPENCLAW_PID_FILE}"
  wait_http "http://127.0.0.1:${OPENCLAW_PORT}/health" "OpenClaw bot" 80
}

onboard_openclaw_bot() {
  local profile_dir="${HOME}/.openclaw-${OPENCLAW_PROFILE}"
  local session_file="${profile_dir}/.bcs/session.json"
  local token="" bot_uuid="" onboard_out="" visibility_out="" last_error=""

  if dry "${BCS_CLI} --url http://127.0.0.1:${BCS_PORT} onboard --token <openclaw-token> --name ${OPENCLAW_BOT_NAME} && visibility set public"; then return 0; fi

  for _ in $(seq 1 90); do
    if load_openclaw_session "${session_file}" "${OPENCLAW_SESSION_MARKER}"; then
      token="${SESSION_TOKEN}"
      bot_uuid="${SESSION_BOT_UUID}"
      break
    fi
    sleep 1
  done
  [[ -n "${token}" && -n "${bot_uuid}" && "${bot_uuid}" != "null" ]] || die "OpenClaw bot did not write BCS token: ${session_file}"

  for attempt in $(seq 1 15); do
    log "onboarding OpenClaw bot ${bot_uuid} (attempt ${attempt})"
    if onboard_out="$("${BCS_CLI}" --url "http://127.0.0.1:${BCS_PORT}" onboard \
      --token "${token}" \
      --name "${OPENCLAW_BOT_NAME}" \
      --summary "${OPENCLAW_SUMMARY}" \
      --domains "${OPENCLAW_DOMAINS}" \
      --skills "${OPENCLAW_SKILLS}" \
      --scopes "${OPENCLAW_SCOPES}" 2>&1)"; then
      [[ -n "${onboard_out}" ]] && printf '%s\n' "${onboard_out}"
      if visibility_out="$("${BCS_CLI}" --url "http://127.0.0.1:${BCS_PORT}" visibility \
        --token "${token}" set --value public --bot-uuid "${bot_uuid}" 2>&1)"; then
        [[ -n "${visibility_out}" ]] && printf '%s\n' "${visibility_out}"
        return 0
      fi
      printf '%s\n' "${visibility_out}" >&2
      return 1
    fi

    last_error="${onboard_out}"
    if printf '%s' "${onboard_out}" | grep -E "401 Unauthorized|valid bot token is required|unauthorized" >/dev/null; then
      log "OpenClaw onboard token was rejected; waiting for refreshed BCS session token"
      sleep 1
      if load_openclaw_session "${session_file}" "${OPENCLAW_SESSION_MARKER}"; then
        token="${SESSION_TOKEN}"
        bot_uuid="${SESSION_BOT_UUID}"
      fi
      continue
    fi

    printf '%s\n' "${onboard_out}" >&2
    return 1
  done

  err "OpenClaw bot onboard failed after token refresh retries"
  [[ -n "${last_error}" ]] && printf '%s\n' "${last_error}" >&2
  exit 1
}

ensure_relay_deps() {
  require_file "${RELAY_DIR}/package.json" "relay package.json"
  if dry "RELAY_DIR=${RELAY_DIR} tnpm install (if node_modules missing)"; then return 0; fi
  if [[ ! -d "${RELAY_DIR}/node_modules" ]]; then
    (cd "${RELAY_DIR}" && tnpm install)
  fi
}

start_relay() {
  log "starting teamclaw-aicoding-relay on ${RELAY_PORT}"
  ensure_relay_deps
  if dry "RELAY_DIR=${RELAY_DIR} PORT=${RELAY_PORT} RELAY_DEFAULT_MODEL=${RELAY_DEFAULT_MODEL} RELAY_DEFAULT_CWD=${CLAUDE_CODE_DEFAULT_CWD} tnpm run dev"; then return 0; fi
  mkdir -p "${CLAUDE_CODE_DEFAULT_CWD}"
  kill_port "${RELAY_PORT}"
  (
    cd "${RELAY_DIR}"
    PORT="${RELAY_PORT}" RELAY_DEFAULT_MODEL="${RELAY_DEFAULT_MODEL}" \
      RELAY_DEFAULT_CWD="${CLAUDE_CODE_DEFAULT_CWD}" \
      nohup tnpm run dev >> "${RELAY_LOG}" 2>&1 &
    echo $! > "${RELAY_PID_FILE}"
  )
  wait_port "${RELAY_PORT}" "relay" 60
}

ensure_engine_deps() {
  require_file "${ENGINE_DIR}/scripts/run.sh" "engine run script"
  if dry "bash ${SINGLEBOX} --engine claude_code setup engine (if .venv missing)"; then return 0; fi
  if [[ ! -x "${ENGINE_DIR}/.venv/bin/python" ]]; then
    CHAT_ENGINE=claude_code bash "${SINGLEBOX}" --engine claude_code setup engine
  fi
  [[ -x "${ENGINE_DIR}/.venv/bin/python" ]] || die "engine venv not found: ${ENGINE_DIR}/.venv"
}

start_engine() {
  log "starting engine adapter claude_code on ${ENGINE_PORT}"
  ensure_engine_deps
  if dry "ZERO_CHECK_ENABLED=false CHAT_ENGINE=claude_code CLAUDE_CODE_DEFAULT_CWD=${CLAUDE_CODE_DEFAULT_CWD} ${ENGINE_DIR}/scripts/run.sh --port ${ENGINE_PORT} -l -e claude_code"; then return 0; fi
  kill_port "${ENGINE_PORT}"
  (
    cd "${ENGINE_DIR}"
    # shellcheck disable=SC1091
    source "${ENGINE_DIR}/.venv/bin/activate"
    ZERO_CHECK_ENABLED=false CHAT_ENGINE=claude_code \
      CLAUDE_CODE_DEFAULT_CWD="${CLAUDE_CODE_DEFAULT_CWD}" \
      RELAY_DEFAULT_CWD="${CLAUDE_CODE_DEFAULT_CWD}" \
      nohup "${ENGINE_DIR}/scripts/run.sh" --port "${ENGINE_PORT}" -l -e claude_code \
      >> "${ENGINE_LOG}" 2>&1 &
    echo $! > "${ENGINE_PID_FILE}"
  )
  wait_port "${ENGINE_PORT}" "engine" 80
}

register_provider() {
  local webhook="http://localhost:${MOCK_PORT}/webhook"
  local reg provider_id admin_token bot bot_uuid provider_payload bot_payload

  if [[ "${DRY_RUN}" == "1" ]]; then
    printf 'DRY RUN: POST /providers webhook_url=%s protocol_version=%s coordination.mode=%s coordination.mcp_server=%s coordination.mcporter_command=%s\n' \
      "${webhook}" "${REG_PROTOCOL_VERSION}" "${PROVIDER_COORDINATION_MODE}" \
      "${PROVIDER_COORDINATION_MCP_SERVER}" "${PROVIDER_COORDINATION_MCPORTER_COMMAND}"
    printf 'DRY RUN: POST /providers/dry-provider-id/bots provider_bot_ref=%s\n' "${PROVIDER_BOT_REF}"
    printf 'DRY RUN: provider_id=dry-provider-id provider_admin_token=<provider_admin_token> bot_uuid=dry-bot-uuid protocol_version=%s\n' "${REG_PROTOCOL_VERSION}"
    PROVIDER_ID="dry-provider-id"
    ADMIN_TOKEN="provider_admin_token"
    BOT_UUID="dry-bot-uuid"
    return 0
  fi

  provider_payload="$(python3 -c 'import json,sys; print(json.dumps({"name":sys.argv[1],"webhook_url":sys.argv[2],"auth":{"mode":"static_bearer"},"protocol_version":sys.argv[3],"coordination":{"mode":sys.argv[4],"mcp_server":sys.argv[5],"mcporter_command":sys.argv[6]}}))' "${PROVIDER_NAME}" "${webhook}" "${REG_PROTOCOL_VERSION}" "${PROVIDER_COORDINATION_MODE}" "${PROVIDER_COORDINATION_MCP_SERVER}" "${PROVIDER_COORDINATION_MCPORTER_COMMAND}")"
  reg="$(api -X POST "http://127.0.0.1:${BCS_PORT}/providers" \
    -H "X-Mock-Staff-No: ${STAFF_NO}" \
    -H 'Content-Type: application/json' \
    -d "${provider_payload}")"
  provider_id="$(printf '%s' "${reg}" | json_get provider_id)"
  admin_token="$(printf '%s' "${reg}" | json_get provider_admin_token)"

  bot_payload="$(python3 -c 'import json,sys; print(json.dumps({"name":sys.argv[1],"provider_bot_ref":sys.argv[2],"owners":[sys.argv[3]],"summary":sys.argv[4],"domains":[p.strip() for p in sys.argv[5].split(",") if p.strip()],"skills":[p.strip() for p in sys.argv[6].split(",") if p.strip()],"scopes":[p.strip() for p in sys.argv[7].split(",") if p.strip()]}))' "${PROVIDER_BOT_NAME}" "${PROVIDER_BOT_REF}" "${STAFF_NO}" "${PROVIDER_BOT_SUMMARY}" "${PROVIDER_BOT_DOMAINS}" "${PROVIDER_BOT_SKILLS}" "${PROVIDER_BOT_SCOPES}")"
  bot="$(api -X POST "http://127.0.0.1:${BCS_PORT}/providers/${provider_id}/bots" \
    -H "Authorization: Bearer ${admin_token}" \
    -H 'Content-Type: application/json' \
    -d "${bot_payload}")"
  bot_uuid="$(printf '%s' "${bot}" | json_get bot_uuid)"

  PROVIDER_ID="${provider_id}"
  ADMIN_TOKEN="${admin_token}"
  BOT_UUID="${bot_uuid}"
  log "provider_id=${PROVIDER_ID} bot_uuid=${BOT_UUID} protocol_version=${REG_PROTOCOL_VERSION}"
}

start_mock_provider_bridge() {
  local bridge_cmd
  bridge_cmd=(
    "${ENGINE_DIR}/.venv/bin/python" "${BRIDGE}"
    --port "${MOCK_PORT}" --engine claude_code
    --engine-host localhost --engine-port "${ENGINE_PORT}"
    --mode "${MOCK_MODE}"
    --permission-mode "${PROVIDER_PERMISSION_MODE}"
  )
  log "starting Provider webhook bridge on ${MOCK_PORT} (mode=${MOCK_MODE})"
  if [[ "${MOCK_MODE}" == callback-* ]]; then
    bridge_cmd+=(
      --bcs-events-url "http://127.0.0.1:${BCS_PORT}/bot/events"
      --provider-id "${PROVIDER_ID}"
      --provider-bot-ref "${PROVIDER_BOT_REF}"
      --provider-admin-token "${ADMIN_TOKEN}"
    )
  fi

  if dry "${bridge_cmd[*]}"; then return 0; fi
  require_file "${BRIDGE}" "mock_provider_bridge.py"
  kill_port "${MOCK_PORT}"
  nohup "${bridge_cmd[@]}" >> "${MOCK_LOG}" 2>&1 &
  echo $! > "${MOCK_PID_FILE}"
  wait_http "http://127.0.0.1:${MOCK_PORT}/health" "mock provider bridge" 60
}

start_frontend() {
  [[ "${START_FRONTEND}" == "1" ]] || { log "frontend skipped (--no-frontend)"; return 0; }
  log "starting external open-claw frontend on ${FRONTEND_PORT}"
  require_file "${FRONTEND_DIR}/package.json" "frontend package.json"
  if dry "FRONTEND_DIR=${FRONTEND_DIR} HUSKY=0 tnpm i (if node_modules missing) && tnpm run ${FRONTEND_DEV_SCRIPT}"; then return 0; fi
  if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
    (cd "${FRONTEND_DIR}" && HUSKY=0 tnpm i)
  fi
  kill_port "${FRONTEND_PORT}"
  (
    cd "${FRONTEND_DIR}"
    tail -f /dev/null | nohup tnpm run "${FRONTEND_DEV_SCRIPT}" >> "${FRONTEND_LOG}" 2>&1 &
    echo $! > "${FRONTEND_PID_FILE}"
  )
  wait_port "${FRONTEND_PORT}" "frontend" 80
  log "frontend URL: http://agentclaw-local.localhost:${FRONTEND_PORT}/"
}

start_all() {
  log "starting mixed BCS topology"
  log "mock BCS user staff_no: ${STAFF_NO}"
  log "relay dir: ${RELAY_DIR}"
  log "frontend dir: ${FRONTEND_DIR}"

  require_file "${SINGLEBOX}" "singlebox.sh"
  require_dir "${RELAY_DIR}" "teamclaw-aicoding-relay"
  require_dir "${FRONTEND_DIR}" "open-claw frontend"
  require_file "${BRIDGE}" "mock_provider_bridge.py"
  preflight_setup_visibility

  export NO_PROXY="${NO_PROXY:+${NO_PROXY},}127.0.0.1,localhost,0.0.0.0"
  export no_proxy="${no_proxy:+${no_proxy},}127.0.0.1,localhost,0.0.0.0"

  setup_bcs_and_plugin
  start_clean_bcs
  start_openclaw_bot
  onboard_openclaw_bot
  start_relay
  start_engine
  register_provider
  start_mock_provider_bridge
  start_frontend

  cat <<EOF

=== mixed BCS chain up ===
OpenClaw WS bot:
  bot_id=${OPENCLAW_BOT_ID}
  gateway=http://127.0.0.1:${OPENCLAW_PORT}

Claude Code Provider bot:
  provider_id=${PROVIDER_ID:-}
  provider_bot_ref=${PROVIDER_BOT_REF}
  bot_uuid=${BOT_UUID:-}
  webhook=http://localhost:${MOCK_PORT}/webhook
  downlink protocol_version=${REG_PROTOCOL_VERSION}
  coordination=${PROVIDER_COORDINATION_MODE} ${PROVIDER_COORDINATION_MCP_SERVER}.${PROVIDER_COORDINATION_MCPORTER_COMMAND}
  bridge mode=${MOCK_MODE}

Frontend:
  http://agentclaw-local.localhost:${FRONTEND_PORT}/

Logs:
  ${OPENCLAW_LOG}
  ${RELAY_LOG}
  ${ENGINE_LOG}
  ${MOCK_LOG}
  ${FRONTEND_LOG}

Stop:
  ${0} stop
EOF
}

stop_all() {
  log "stopping mixed provider chain"
  kill_pid_file "${FRONTEND_PID_FILE}" "frontend"
  kill_port "${FRONTEND_PORT}"
  if [[ "${DRY_RUN}" != "1" ]]; then
    pkill -f "bigfish.*${FRONTEND_DIR}" 2>/dev/null || true
  fi

  kill_pid_file "${MOCK_PID_FILE}" "mock provider bridge"
  kill_port "${MOCK_PORT}"
  kill_pid_file "${ENGINE_PID_FILE}" "engine"
  kill_port "${ENGINE_PORT}"
  if [[ "${DRY_RUN}" != "1" ]]; then
    pkill -f "engine.api.app" 2>/dev/null || true
  fi

  kill_pid_file "${RELAY_PID_FILE}" "relay"
  kill_port "${RELAY_PORT}"
  kill_pid_file "${OPENCLAW_PID_FILE}" "OpenClaw bot"
  kill_port "${OPENCLAW_PORT}"

  if dry "BCS_AUTO_ONBOARD=0 bash ${SINGLEBOX} stop bcs"; then return 0; fi
  BCS_PORT="${BCS_PORT}" BCS_AUTO_ONBOARD=0 bash "${SINGLEBOX}" stop bcs || true
  log "mixed provider chain stopped"
}

status_one() {
  local label="$1" port="$2" pid
  pid="$(port_pids "${port}" | head -1)"
  if [[ -n "${pid}" ]]; then
    printf '  %-24s running pid=%s port=%s\n' "${label}" "${pid}" "${port}"
  else
    printf '  %-24s stopped port=%s\n' "${label}" "${port}"
  fi
}

status_all() {
  status_one "BCS" "${BCS_PORT}"
  status_one "OpenClaw WS bot" "${OPENCLAW_PORT}"
  status_one "relay" "${RELAY_PORT}"
  status_one "engine claude_code" "${ENGINE_PORT}"
  status_one "Provider bridge" "${MOCK_PORT}"
  status_one "frontend" "${FRONTEND_PORT}"
}

case "${CMD}" in
  start) start_all ;;
  stop) stop_all ;;
  status) status_all ;;
  *) die "unsupported command: ${CMD}" ;;
esac
