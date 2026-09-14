#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$(id -u)" == "0" ]]; then
  exec runuser -u admin -- bash "$0" "$@"
fi
if [[ "$(id -un)" != "admin" ]]; then
  printf '{"ok":false,"error":"initializer must execute as admin"}\n' >&2
  exit 1
fi

TASK_ID=""
STEP_ID=""
CLAWWEB_URL=""
RUNTIME_MAINTENANCE="true"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --task-id) TASK_ID="${2:-}"; shift 2 ;;
    --step-id) STEP_ID="${2:-}"; shift 2 ;;
    --clawweb-url) CLAWWEB_URL="${2:-}"; shift 2 ;;
    --runtime-maintenance) RUNTIME_MAINTENANCE="${2:-}"; shift 2 ;;
    *) printf '{"ok":false,"error":"unknown argument"}\n' >&2; exit 2 ;;
  esac
done

[[ "$RUNTIME_MAINTENANCE" == "true" || "$RUNTIME_MAINTENANCE" == "false" ]] || {
  printf '{"ok":false,"error":"runtime-maintenance must be true or false"}\n' >&2
  exit 2
}

[[ -n "$TASK_ID" && ${#TASK_ID} -le 256 && "$TASK_ID" =~ ^[A-Za-z0-9._:-]+$ ]] || {
  printf '{"ok":false,"error":"invalid task-id"}\n' >&2
  exit 2
}
[[ -n "$STEP_ID" && ${#STEP_ID} -le 256 && "$STEP_ID" =~ ^[A-Za-z0-9._:-]+$ ]] || {
  printf '{"ok":false,"error":"invalid step-id"}\n' >&2
  exit 2
}
CLAWWEB_URL="$(python3 - "$CLAWWEB_URL" <<'PY'
import sys
from urllib.parse import urlsplit

raw = sys.argv[1].strip().rstrip("/")
parsed = urlsplit(raw)
local = parsed.hostname in {"localhost", "127.0.0.1"}
valid = (
    parsed.scheme in ({"http", "https"} if local else {"https"})
    and parsed.hostname is not None
    and (local or parsed.scheme == "https")
    and parsed.username is None and parsed.password is None
    and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
)
if not valid:
    raise SystemExit(2)
print(raw)
PY
)" || {
  printf '{"ok":false,"error":"invalid clawweb-url"}\n' >&2
  exit 2
}

RUNNER="${SCRIPT_DIR}/clawevolve_async_runner.sh"
[[ -f "$RUNNER" && -r "$RUNNER" ]] || {
  printf '{"ok":false,"error":"clawevolve_async_runner.sh not found or unreadable"}\n' >&2
  exit 1
}

RESULTS_ROOT="${CLAWEVOLVE_RESULTS_ROOT:-/home/admin/.openclaw/workspace/clawevolve_results}"
LOG_DIR="${RESULTS_ROOT}/${TASK_ID}/skill-init/output"
LOG_FILE="${LOG_DIR}/clawevolve-skill-init.log"
RESULT_FILE="${LOG_DIR}/runner-init-result.json"
COMPLETED_FILE="${LOG_DIR}/completed"
LAUNCH_PID_FILE="${LOG_DIR}/launcher.pid"
mkdir -p "$LOG_DIR"
log_line() {
  printf '%s [clawevolve-skill-init] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >> "$LOG_FILE"
}

post_report() {
  local status="$1" summary="$2" code="${3:-}" message="${4:-}" result="${5:-}" release_version="${6:-}"
  python3 - "$CLAWWEB_URL" "$TASK_ID" "$STEP_ID" "$status" "$summary" "$code" "$message" "$result" "$release_version" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request

base_url, task_id, step_id, status, summary, code, message, result, release_version = sys.argv[1:]
payload = {"status": status, "summary": summary}
if status == "succeeded":
    payload["output"] = {
        "schemaVersion": "clawevolve.skill-init.v1",
        "result": result,
        "releaseVersion": release_version,
        "user": "admin",
        "transport": "arca_message_exec",
    }
elif status == "failed":
    payload["error"] = {"code": code, "message": message, "retryable": True}
data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
url = f"{base_url}/api/evolve/internal/tasks/{task_id}/steps/{step_id}/report"
last_error = None
for attempt in range(4):
    try:
        request = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            if 200 <= response.status < 300:
                raise SystemExit(0)
            last_error = f"HTTP {response.status}"
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        last_error = str(exc)
    if attempt < 3:
        time.sleep(1)
print(f"step report failed: {last_error}", file=sys.stderr)
raise SystemExit(1)
PY
}

read_result_file() {
  python3 - "$RESULT_FILE" <<'PY'
import json
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
result = str(value.get("result") or "")
release = str(value.get("release_version") or "")
if result not in {"installed", "unchanged"}:
    raise SystemExit(1)
if not release or len(release) > 128 or not all(char.isalnum() or char in "._-" for char in release):
    raise SystemExit(1)
print(f"{result}\t{release}")
PY
}

if [[ "${CLAWEVOLVE_INITIALIZER_FINALIZE:-0}" == "1" ]]; then
  FINAL_RESULT="$(read_result_file)" || {
    post_report "failed" "ClawEvolve Skill 初始化失败" "ARCA_SKILL_INIT_FAILED" "initializer result file is missing or invalid" || true
    exit 1
  }
  IFS=$'\t' read -r RUNNER_RESULT RELEASE_VERSION <<< "$FINAL_RESULT"
  log_line "runtime maintenance completed; reporting success result=${RUNNER_RESULT} release=${RELEASE_VERSION}"
  post_report "succeeded" "ClawEvolve Skill 初始化完成" "" "" "$RUNNER_RESULT" "$RELEASE_VERSION"
  touch "$COMPLETED_FILE"
  python3 - "$RUNNER_RESULT" "$RELEASE_VERSION" <<'PY'
import json, sys
print(json.dumps({"ok": True, "status": "initialized", "result": sys.argv[1], "release_version": sys.argv[2]}, separators=(",", ":")))
PY
  exit 0
fi

log_line "initialization started task_id=${TASK_ID} step_id=${STEP_ID} runner=${RUNNER}"
if ! post_report "running" "正在同步 ClawEvolve Skill"; then
  log_line "running report failed; initialization continues"
fi

RUNNER_OUTPUT=""
RUNNER_STATUS=""
RUNNER_RESULT=""
RELEASE_VERSION=""
RUNNER_ERROR=""
for attempt in $(seq 1 30); do
  STDERR_FILE="$(mktemp /tmp/clawevolve-message-init.XXXXXX)"
  set +e
  RUNNER_OUTPUT="$(bash "$RUNNER" --stage init --args-base64 '' 2> "$STDERR_FILE")"
  RUNNER_EXIT=$?
  set -e
  if [[ -s "$STDERR_FILE" ]]; then
    cat "$STDERR_FILE" >> "$LOG_FILE"
    RUNNER_ERROR="$(tail -n 20 "$STDERR_FILE" | tr '\n' ' ' | cut -c1-2000)"
  fi
  rm -f "$STDERR_FILE"
  PARSED="$(python3 - "$RUNNER_OUTPUT" <<'PY'
import json
import sys

parsed = None
for line in reversed(sys.argv[1].splitlines()):
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        continue
    if isinstance(value, dict):
        parsed = value
        break
if parsed is None:
    raise SystemExit(1)
print("\t".join(
    str(parsed.get(key) or "").replace("\t", " ").replace("\n", " ")
    for key in ("status", "result", "release_version", "error")
))
PY
)" || PARSED=""
  if [[ -n "$PARSED" ]]; then
    IFS=$'\t' read -r RUNNER_STATUS RUNNER_RESULT RELEASE_VERSION PARSED_ERROR <<< "$PARSED"
    [[ -n "${PARSED_ERROR:-}" ]] && RUNNER_ERROR="$PARSED_ERROR"
  fi
  if (( RUNNER_EXIT == 0 )) && [[ "$RUNNER_STATUS" == "initialized" ]]; then
    break
  fi
  if (( RUNNER_EXIT != 0 )) || [[ "$RUNNER_STATUS" != "starting" ]]; then
    break
  fi
  log_line "initializer lock busy attempt=${attempt}; retrying"
  sleep 2
done

if [[ "$RUNNER_STATUS" != "initialized" \
  || ! "$RUNNER_RESULT" =~ ^(installed|unchanged)$ \
  || -z "$RELEASE_VERSION" || ${#RELEASE_VERSION} -gt 128 \
  || ! "$RELEASE_VERSION" =~ ^[A-Za-z0-9._-]+$ ]]; then
  ERROR_MESSAGE="${RUNNER_ERROR:-runner init result invalid or unavailable}"
  log_line "initialization failed exit=${RUNNER_EXIT:-1} status=${RUNNER_STATUS:-missing} error=${ERROR_MESSAGE}"
  post_report "failed" "ClawEvolve Skill 初始化失败" "ARCA_SKILL_INIT_FAILED" "$ERROR_MESSAGE" || true
  python3 - "$ERROR_MESSAGE" <<'PY'
import json, sys
print(json.dumps({"ok": False, "error": sys.argv[1]}, ensure_ascii=False, separators=(",", ":")))
PY
  exit 1
fi

log_line "initialization completed result=${RUNNER_RESULT} release=${RELEASE_VERSION}"
RESULT_TMP="${RESULT_FILE}.tmp.$$"
python3 - "$RESULT_TMP" "$RUNNER_RESULT" "$RELEASE_VERSION" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({"result": sys.argv[2], "release_version": sys.argv[3]}, separators=(",", ":")) + "\n", encoding="utf-8")
PY
chmod 600 "$RESULT_TMP"
mv "$RESULT_TMP" "$RESULT_FILE"

if [[ -f "$COMPLETED_FILE" ]]; then
  CLAWEVOLVE_INITIALIZER_FINALIZE=1 exec bash "$0" \
    --task-id "$TASK_ID" --step-id "$STEP_ID" --clawweb-url "$CLAWWEB_URL" \
    --runtime-maintenance "$RUNTIME_MAINTENANCE"
fi

EXISTING_PID="$(tr -cd '0-9' < "$LAUNCH_PID_FILE" 2>/dev/null || true)"
if [[ -n "$EXISTING_PID" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
  printf '{"ok":true,"status":"starting","pid":%s,"task_id":"%s","step_id":"%s"}\n' "$EXISTING_PID" "$TASK_ID" "$STEP_ID"
  exit 0
fi

TASK_LAUNCHER="${SCRIPT_DIR}/clawevolve_task_launcher.sh"
[[ -x "$TASK_LAUNCHER" ]] || {
  post_report "failed" "ClawEvolve Skill 初始化失败" "ARCA_SKILL_INIT_FAILED" "clawevolve task launcher not found or not executable" || true
  printf '{"ok":false,"error":"clawevolve task launcher not found or not executable"}\n' >&2
  exit 1
}
setsid nohup bash "$TASK_LAUNCHER" \
  --task-id "$TASK_ID" \
  --step-id "$STEP_ID" \
  --log-file "$LOG_FILE" \
  --clawweb-url "$CLAWWEB_URL" \
  --runtime-maintenance "$RUNTIME_MAINTENANCE" \
  -- env CLAWEVOLVE_INITIALIZER_FINALIZE=1 CLAWEVOLVE_RESULTS_ROOT="$RESULTS_ROOT" \
    bash "$0" --task-id "$TASK_ID" --step-id "$STEP_ID" --clawweb-url "$CLAWWEB_URL" \
      --runtime-maintenance "$RUNTIME_MAINTENANCE" \
  >> "$LOG_FILE" 2>&1 < /dev/null &
LAUNCH_PID=$!
printf '%s\n' "$LAUNCH_PID" > "$LAUNCH_PID_FILE"
log_line "runtime maintenance launcher started pid=${LAUNCH_PID}"
printf '{"ok":true,"status":"started","pid":%s,"task_id":"%s","step_id":"%s"}\n' "$LAUNCH_PID" "$TASK_ID" "$STEP_ID"
