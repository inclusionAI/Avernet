#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER_ENVIRONMENT="${SCRIPT_DIR}/platform/clawevolve_runtime/runner_environment.py"
[[ -r "$RUNNER_ENVIRONMENT" ]] || RUNNER_ENVIRONMENT="${SCRIPT_DIR}/../platform/clawevolve_runtime/runner_environment.py"
TASK_ID=""
STEP_ID=""
LOG_FILE=""
RUN_CWD=""
CLAWWEB_URL=""
RUNTIME_MAINTENANCE="true"
PREFLIGHT_ACTIVE_EVOLVE_GUARD="false"
REFRESH_GATEWAY_BEFORE_HANDLER="false"
GATEWAY_RESTARTED_FOR_TASK="false"

on_exit() {
  local status="$1"
  trap - EXIT
  if (( status != 0 && ${BASH_SUBSHELL:-0} == 0 )) \
    && [[ -n "$TASK_ID" && -n "$STEP_ID" && -n "$CLAWWEB_URL" ]]; then
    python3 "$SCRIPT_DIR/clawevolve_startup_failure.py" \
      --clawweb-url "$CLAWWEB_URL" --task-id "$TASK_ID" --step-id "$STEP_ID" \
      --phase launcher --exit-code "$status" \
      || printf 'Task startup failed; failure report was not acknowledged\n' >&2
  fi
  return "$status"
}
# Successful exec replaces this shell and clears the trap. Until then every
# nonzero exit, including explicit exit and failed exec, belongs to startup.
trap 'on_exit $?' EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

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

RUNNER_ENVIRONMENT_SETUP="$(python3 "$RUNNER_ENVIRONMENT" shell-init)"
eval "$RUNNER_ENVIRONMENT_SETUP"
python3 "$RUNNER_ENVIRONMENT" prepare-task --script-directory "$SCRIPT_DIR" -- \
  --task-id "$TASK_ID" --step-id "$STEP_ID" --log-file "$LOG_FILE" \
  --runtime-maintenance "$RUNTIME_MAINTENANCE" \
  --preflight-active-evolve-guard "$PREFLIGHT_ACTIVE_EVOLVE_GUARD" \
  --refresh-gateway-before-handler "$REFRESH_GATEWAY_BEFORE_HANDLER"
if [[ -n "$RUN_CWD" ]]; then
  cd "$RUN_CWD"
fi
log_line "runtime ready; starting handler"
# Bash 3 can clear EXIT even when exec fails. Allow exec to return, then report
# that failure explicitly; successful exec never reaches these last two lines.
shopt -s execfail
set +e
exec "${HANDLER_COMMAND[@]}"
on_exit "$?"
exit "$?"
