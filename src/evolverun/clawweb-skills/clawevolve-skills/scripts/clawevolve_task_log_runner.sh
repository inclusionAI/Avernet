#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER_ENVIRONMENT="${SCRIPT_DIR}/platform/clawevolve_runtime/runner_environment.py"
[[ -r "$RUNNER_ENVIRONMENT" ]] || RUNNER_ENVIRONMENT="${SCRIPT_DIR}/../platform/clawevolve_runtime/runner_environment.py"
RUNNER_ENVIRONMENT_SETUP="$(python3 "$RUNNER_ENVIRONMENT" shell-init -- "$0" "$@")"
eval "$RUNNER_ENVIRONMENT_SETUP"
TASK_ID=""
ARCHIVE_ID=""
CLAWWEB_URL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --task-id) TASK_ID="${2:-}"; shift 2 ;;
    --archive-id) ARCHIVE_ID="${2:-}"; shift 2 ;;
    --clawweb-url) CLAWWEB_URL="${2:-}"; shift 2 ;;
    *) printf 'unknown task log runner argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ -n "$TASK_ID" && ${#TASK_ID} -le 128 && "$TASK_ID" =~ ^[A-Za-z0-9._:-]+$ ]] || { printf 'invalid task-id\n' >&2; exit 2; }
[[ -n "$ARCHIVE_ID" && ${#ARCHIVE_ID} -le 128 && "$ARCHIVE_ID" =~ ^[A-Za-z0-9._:-]+$ ]] || { printf 'invalid archive-id\n' >&2; exit 2; }
[[ "$CLAWWEB_URL" =~ ^https?://[^[:space:]]+$ ]] || { printf 'invalid clawweb-url\n' >&2; exit 2; }

if [[ "$(id -u)" == "0" ]]; then
  exec runuser -u admin -- "$0" --task-id "$TASK_ID" --archive-id "$ARCHIVE_ID" --clawweb-url "$CLAWWEB_URL"
fi

COLLECTOR="${SCRIPT_DIR}/collect_clawevolve_task_logs.py"
[[ -r "$COLLECTOR" ]] || { printf 'task log collector not found: %s\n' "$COLLECTOR" >&2; exit 1; }
STATE_DIR="/tmp/clawevolve-task-log-runner/${ARCHIVE_ID}"
mkdir -p "$STATE_DIR"
if [[ -s "$STATE_DIR/pid" ]]; then
  EXISTING_PID="$(tr -cd '0-9' < "$STATE_DIR/pid")"
  if [[ -n "$EXISTING_PID" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    printf '{"ok":true,"status":"already_started","pid":%s,"task_id":"%s","archive_id":"%s"}\n' "$EXISTING_PID" "$TASK_ID" "$ARCHIVE_ID"
    exit 0
  fi
fi
setsid nohup python3 -u -B "$COLLECTOR" \
  --task-id "$TASK_ID" --archive-id "$ARCHIVE_ID" --clawweb-url "$CLAWWEB_URL" \
  >"$STATE_DIR/runner.log" 2>&1 </dev/null &
PID=$!
printf '%s\n' "$PID" > "$STATE_DIR/pid"
printf '{"ok":true,"status":"started","pid":%s,"task_id":"%s","archive_id":"%s"}\n' "$PID" "$TASK_ID" "$ARCHIVE_ID"
