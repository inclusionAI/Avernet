#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASK_ID=""
STEP_ID=""
LOG_FILE=""
RUN_CWD=""
CLAWWEB_URL=""
RUNTIME_MAINTENANCE="true"
PREFLIGHT_ACTIVE_EVOLVE_GUARD="false"
REFRESH_GATEWAY_BEFORE_HANDLER="false"
GATEWAY_RESTARTED_FOR_TASK="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --task-id) TASK_ID="${2:-}"; shift 2 ;;
    --step-id) STEP_ID="${2:-}"; shift 2 ;;
    --log-file) LOG_FILE="${2:-}"; shift 2 ;;
    --run-cwd) RUN_CWD="${2:-}"; shift 2 ;;
    --clawweb-url) CLAWWEB_URL="${2:-}"; shift 2 ;;
    --runtime-maintenance) RUNTIME_MAINTENANCE="${2:-}"; shift 2 ;;
    --preflight-active-evolve-guard) PREFLIGHT_ACTIVE_EVOLVE_GUARD="${2:-}"; shift 2 ;;
    --refresh-gateway-before-handler) REFRESH_GATEWAY_BEFORE_HANDLER="${2:-}"; shift 2 ;;
    --) shift; break ;;
    *) printf 'unknown task launcher argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

(( $# > 0 )) || { printf 'task launcher command is required\n' >&2; exit 2; }
[[ "$TASK_ID" =~ ^[A-Za-z0-9._:-]{1,256}$ ]] || { printf 'invalid task-id\n' >&2; exit 2; }
[[ "$STEP_ID" =~ ^[A-Za-z0-9._:-]{1,256}$ ]] || { printf 'invalid step-id\n' >&2; exit 2; }
[[ -n "$LOG_FILE" ]] || { printf 'log-file is required\n' >&2; exit 2; }
[[ "$RUNTIME_MAINTENANCE" == "true" || "$RUNTIME_MAINTENANCE" == "false" ]] || {
  printf 'runtime-maintenance must be true or false\n' >&2
  exit 2
}
[[ "$PREFLIGHT_ACTIVE_EVOLVE_GUARD" == "true" || "$PREFLIGHT_ACTIVE_EVOLVE_GUARD" == "false" ]] || {
  printf 'preflight-active-evolve-guard must be true or false\n' >&2
  exit 2
}
[[ "$REFRESH_GATEWAY_BEFORE_HANDLER" == "true" || "$REFRESH_GATEWAY_BEFORE_HANDLER" == "false" ]] || {
  printf 'refresh-gateway-before-handler must be true or false\n' >&2
  exit 2
}
HANDLER_COMMAND=("$@")

log_line() {
  printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >> "$LOG_FILE"
}

wait_for_openclaw_gateway() {
  local config_path="/home/admin/.openclaw/openclaw.json"
  local timeout_seconds="${CLAWEVOLVE_GATEWAY_READY_TIMEOUT_SECONDS:-60}"
  local interval_seconds="${CLAWEVOLVE_GATEWAY_READY_INTERVAL_SECONDS:-2}"
  local gateway_port deadline attempt=0 supervisor_status="" last_error=""
  [[ "$timeout_seconds" =~ ^[1-9][0-9]*$ ]] || {
    printf 'invalid CLAWEVOLVE_GATEWAY_READY_TIMEOUT_SECONDS\n' >&2
    return 1
  }
  [[ "$interval_seconds" =~ ^[1-9][0-9]*$ ]] || {
    printf 'invalid CLAWEVOLVE_GATEWAY_READY_INTERVAL_SECONDS\n' >&2
    return 1
  }
  gateway_port="$(python3 - "$config_path" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        port = int(json.load(handle).get("gateway", {}).get("port", 18789))
except (OSError, ValueError, TypeError, json.JSONDecodeError):
    port = 18789
if not 1 <= port <= 65535:
    raise SystemExit(1)
print(port)
PY
)" || {
    printf 'failed to resolve OpenClaw gateway port\n' >&2
    return 1
  }
  deadline=$((SECONDS + timeout_seconds))
  log_line "OpenClaw gateway readiness wait start: port=${gateway_port} timeout_seconds=${timeout_seconds}"
  while (( SECONDS < deadline )); do
    attempt=$((attempt + 1))
    supervisor_status="$(sudo -n supervisorctl status openclaw 2>&1 || true)"
    if [[ "$supervisor_status" == *"RUNNING"* ]]; then
      if python3 - "$gateway_port" <<'PY'
import socket
import sys

with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1):
    pass
PY
      then
        log_line "OpenClaw gateway ready: port=${gateway_port} attempts=${attempt} supervisor_status=${supervisor_status}"
        return 0
      fi
      last_error="gateway TCP port ${gateway_port} is not accepting connections"
    else
      last_error="supervisor status is not RUNNING: ${supervisor_status:-empty}"
    fi
    sleep "$interval_seconds"
  done
  log_line "OpenClaw gateway readiness timed out: attempts=${attempt} error=${last_error}"
  printf 'OpenClaw gateway readiness timed out after %s seconds: %s\n' "$timeout_seconds" "$last_error" >&2
  return 1
}

restart_openclaw_gateway_once() {
  if [[ "$GATEWAY_RESTARTED_FOR_TASK" == "true" ]]; then
    log_line "OpenClaw gateway refresh skipped: already restarted for this task"
    return 0
  fi
  sudo -n supervisorctl restart openclaw >> "$LOG_FILE" 2>&1
  log_line "OpenClaw gateway restarted; waiting for readiness"
  wait_for_openclaw_gateway
  GATEWAY_RESTARTED_FOR_TASK="true"
}

report_startup_failure() {
  local message="$1"
  [[ -n "$CLAWWEB_URL" ]] || return 0
  python3 - "$CLAWWEB_URL" "$TASK_ID" "$STEP_ID" "$message" >> "$LOG_FILE" 2>&1 <<'PY' || true
import json
import sys
import urllib.request

base, task_id, step_id, message = sys.argv[1:]
url = f"{base.rstrip('/')}/api/evolve/internal/tasks/{task_id}/steps/{step_id}/report"
payload = {
    "status": "failed",
    "summary": "OpenClaw运行时准备失败",
    "error": {
        "code": "OPENCLAW_RUNTIME_MAINTENANCE_FAILED",
        "message": message[:4000],
        "retryable": True,
    },
}
request = urllib.request.Request(
    url,
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=30) as response:
    print(json.dumps({"runtime_maintenance_failure_report_status": response.status}))
PY
}

on_error() {
  local status=$?
  local line="${BASH_LINENO[0]:-unknown}"
  local failed_command="${BASH_COMMAND:-unknown}"
  trap - ERR
  local message="runtime maintenance or handler launch failed: exit=${status} line=${line} command=${failed_command}"
  log_line "$message"
  report_startup_failure "$message"
  exit "$status"
}
trap on_error ERR

ensure_openclaw_environment() {
  local config_path="/home/admin/.openclaw/openclaw.json"
  local marker_path="/home/admin/.openclaw/workspace/clawevolve_results/.environment_initialized.json"
  local adapter="${SCRIPT_DIR}/adapt_openclaw_environment.py"
  local result status backup_path
  [[ -r "$adapter" ]] || { printf 'OpenClaw environment adapter not found: %s\n' "$adapter" >&2; return 1; }
  result="$(python3 "$adapter" --config "$config_path" --marker "$marker_path")"
  status="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["status"])' "$result")"
  log_line "OpenClaw environment check: ${result}"
  [[ "$status" == "changed" ]] || return 0
  backup_path="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["backup_path"])' "$result")"
  if sudo -n supervisorctl restart openclaw >> "$LOG_FILE" 2>&1; then
    wait_for_openclaw_gateway
    python3 "$adapter" --config "$config_path" --marker "$marker_path" --mark-ready >> "$LOG_FILE"
    GATEWAY_RESTARTED_FOR_TASK="true"
    log_line "OpenClaw gateway restarted and ready after environment adaptation"
    return 0
  fi
  python3 "$adapter" --config "$config_path" --restore "$backup_path" >> "$LOG_FILE" 2>&1
  sudo -n supervisorctl restart openclaw >> "$LOG_FILE" 2>&1 || true
  printf 'OpenClaw environment restart failed and configuration was restored\n' >&2
  return 1
}

assert_no_other_active_evolve_children() {
  local results_root="/home/admin/.openclaw/workspace/clawevolve_results"
  local pid_file pid step_id
  while IFS= read -r pid_file; do
    pid="$(tr -cd '0-9' < "$pid_file" 2>/dev/null || true)"
    [[ -n "$pid" && "$pid" != "$$" ]] || continue
    step_id="$(basename "$(dirname "$pid_file")")"
    if kill -0 "$pid" 2>/dev/null \
      && [[ -r "/proc/$pid/cmdline" ]] \
      && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq -- "$step_id"; then
      printf 'runtime maintenance requires no other active Evolve task; active pid=%s\n' "$pid" >&2
      return 1
    fi
  done < <(find "$results_root" -path '*/runner_state/*/pid' -type f 2>/dev/null || true)
}

write_runtime_maintenance_marker() {
  local marker_path="$1"
  local cleanup_result="$2"
  local gateway_restarted="$3"
  local marker_tmp="${marker_path}.tmp.$$"
  mkdir -p "$(dirname "$marker_path")"
  python3 - "$marker_tmp" "$TASK_ID" "$cleanup_result" "$gateway_restarted" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, task_id, raw_cleanup, raw_gateway_restarted = sys.argv[1:]
try:
    cleanup = json.loads(raw_cleanup)
except (TypeError, json.JSONDecodeError):
    cleanup = {
        "status": "degraded",
        "failures": [{"stage": "launcher", "error": raw_cleanup[-4000:]}],
    }
with open(path, "w", encoding="utf-8") as handle:
    json.dump({
        "schema_version": "clawevolve.runtime_maintenance.v1",
        "task_id": task_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "cleanup": cleanup,
        "gateway_restarted": raw_gateway_restarted == "true",
    }, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
  mv "$marker_tmp" "$marker_path"
}

ensure_task_runtime_maintenance() {
  local marker_path="/home/admin/.openclaw/workspace/clawevolve_results/${TASK_ID}/runner_state/.runtime_maintenance_v1.json"
  local cleaner="${SCRIPT_DIR}/cleanup_clawevolve_openclaw_runtime.py"
  local cleanup_result cleanup_exit active_check_error
  if [[ "$RUNTIME_MAINTENANCE" == "false" ]]; then
    log_line "runtime maintenance skipped: disabled"
    return 0
  fi
  if [[ -f "$marker_path" ]]; then
    log_line "runtime maintenance skipped: task marker exists path=${marker_path}"
    return 0
  fi
  if ! active_check_error="$(assert_no_other_active_evolve_children 2>&1)"; then
    cleanup_result="$(python3 - "$active_check_error" <<'PY'
import json
import sys
print(json.dumps({
    "status": "skipped",
    "reason": "other_active_evolve_task",
    "detail": sys.argv[1][-4000:],
}, ensure_ascii=False))
PY
)"
    log_line "runtime maintenance skipped to protect active task: ${cleanup_result}"
    write_runtime_maintenance_marker "$marker_path" "$cleanup_result" false
    return 0
  fi
  log_line "runtime maintenance start: task=${TASK_ID}"
  restart_openclaw_gateway_once
  if [[ ! -r "$cleaner" ]]; then
    cleanup_result="$(python3 - "$cleaner" <<'PY'
import json
import sys
print(json.dumps({
    "status": "degraded",
    "failures": [{"stage": "launcher", "error": f"cleanup script not found: {sys.argv[1]}"}],
}, ensure_ascii=False))
PY
)"
    log_line "runtime maintenance cleanup warning: ${cleanup_result}"
  elif cleanup_result="$(python3 "$cleaner" --openclaw-home /home/admin/.openclaw 2>&1)"; then
    log_line "runtime maintenance cleanup done: ${cleanup_result}"
  else
    cleanup_exit=$?
    cleanup_result="$(python3 - "$cleanup_exit" "$cleanup_result" <<'PY'
import json
import sys
exit_code, raw = sys.argv[1:]
try:
    detail = json.loads(raw)
except (TypeError, json.JSONDecodeError):
    detail = raw[-4000:]
print(json.dumps({
    "status": "degraded",
    "failures": [{"stage": "launcher", "exit_code": int(exit_code), "error": detail}],
}, ensure_ascii=False))
PY
)"
    log_line "runtime maintenance cleanup warning: ${cleanup_result}"
  fi
  write_runtime_maintenance_marker "$marker_path" "$cleanup_result" "$GATEWAY_RESTARTED_FOR_TASK"
  log_line "runtime maintenance completed: marker=${marker_path}"
}

ENVIRONMENT_LOCK="/home/admin/.openclaw/workspace/clawevolve_results/.environment.lock"
mkdir -p "$(dirname "$ENVIRONMENT_LOCK")"
exec 9> "$ENVIRONMENT_LOCK"
flock 9
SKIP_ENVIRONMENT_ADAPTATION="false"
if [[ "$PREFLIGHT_ACTIVE_EVOLVE_GUARD" == "true" ]]; then
  if ! active_check_error="$(assert_no_other_active_evolve_children 2>&1)"; then
    SKIP_ENVIRONMENT_ADAPTATION="true"
    log_line "environment adaptation skipped to protect active Evolve task: ${active_check_error}"
  fi
fi
if [[ "$SKIP_ENVIRONMENT_ADAPTATION" == "false" ]]; then
  ensure_openclaw_environment
fi
if [[ "$REFRESH_GATEWAY_BEFORE_HANDLER" == "true" && "$SKIP_ENVIRONMENT_ADAPTATION" == "false" ]]; then
  restart_openclaw_gateway_once
fi
ensure_task_runtime_maintenance
flock -u 9
exec 9>&-

trap - ERR
if [[ -n "$RUN_CWD" ]]; then
  cd "$RUN_CWD"
fi
log_line "runtime ready; starting handler"
exec "${HANDLER_COMMAND[@]}"
