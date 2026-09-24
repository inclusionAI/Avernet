#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER_ENVIRONMENT="${SCRIPT_DIR}/platform/clawevolve_runtime/runner_environment.py"
[[ -r "$RUNNER_ENVIRONMENT" ]] || RUNNER_ENVIRONMENT="${SCRIPT_DIR}/../platform/clawevolve_runtime/runner_environment.py"
RUNNER_ENVIRONMENT_SETUP="$(python3 "$RUNNER_ENVIRONMENT" shell-init)"
eval "$RUNNER_ENVIRONMENT_SETUP"
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
[[ -n "$TASK_ID" && ${#TASK_ID} -le 256 && "$TASK_ID" =~ ^[A-Za-z0-9._:-]+$ ]] || { printf 'invalid task-id\n' >&2; exit 2; }
[[ -n "$STEP_ID" && ${#STEP_ID} -le 256 && "$STEP_ID" =~ ^[A-Za-z0-9._:-]+$ ]] || { printf 'invalid step-id\n' >&2; exit 2; }
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

python3 "$RUNNER_ENVIRONMENT" prepare-task --script-directory "$SCRIPT_DIR" -- \
  --task-id "$TASK_ID" --step-id "$STEP_ID" --log-file "$LOG_FILE" \
  --runtime-maintenance "$RUNTIME_MAINTENANCE" \
  --preflight-active-evolve-guard "$PREFLIGHT_ACTIVE_EVOLVE_GUARD" \
  --refresh-gateway-before-handler "$REFRESH_GATEWAY_BEFORE_HANDLER"
trap - ERR
if [[ -n "$RUN_CWD" ]]; then
  cd "$RUN_CWD"
fi
log_line "runtime ready; starting handler"
exec "${HANDLER_COMMAND[@]}"
