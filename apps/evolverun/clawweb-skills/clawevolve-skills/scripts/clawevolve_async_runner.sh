#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The parent supplies its validated callback context before acknowledging dispatch.
# Install the exit guard before environment initialization in the detached child.
BOOTSTRAP_REPORT_TASK_ID="${CLAWEVOLVE_BOOTSTRAP_TASK_ID:-}"
BOOTSTRAP_REPORT_STEP_ID="${CLAWEVOLVE_BOOTSTRAP_STEP_ID:-}"
BOOTSTRAP_REPORT_URL="${CLAWEVOLVE_BOOTSTRAP_URL:-}"
unset CLAWEVOLVE_BOOTSTRAP_TASK_ID CLAWEVOLVE_BOOTSTRAP_STEP_ID CLAWEVOLVE_BOOTSTRAP_URL
report_bootstrap_exit() {
  local status="$1"
  if (( status != 0 && ${BASH_SUBSHELL:-0} == 0 )) \
    && [[ -n "$BOOTSTRAP_REPORT_TASK_ID" && -n "$BOOTSTRAP_REPORT_STEP_ID" && -n "$BOOTSTRAP_REPORT_URL" ]]; then
    python3 "$SCRIPT_DIR/clawevolve_startup_failure.py" \
      --clawweb-url "$BOOTSTRAP_REPORT_URL" --task-id "$BOOTSTRAP_REPORT_TASK_ID" \
      --step-id "$BOOTSTRAP_REPORT_STEP_ID" --phase bootstrap --exit-code "$status" \
      || printf 'Runner startup failed; failure report was not acknowledged\n' >&2
  fi
  return "$status"
}
trap 'report_bootstrap_exit $?' EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

RUNNER_ENVIRONMENT="${SCRIPT_DIR}/platform/clawevolve_runtime/runner_environment.py"
[[ -r "$RUNNER_ENVIRONMENT" ]] || RUNNER_ENVIRONMENT="${SCRIPT_DIR}/../platform/clawevolve_runtime/runner_environment.py"
RUNNER_ENVIRONMENT_SETUP="$(python3 "$RUNNER_ENVIRONMENT" shell-init --check-user -- "$0" "$@")"
eval "$RUNNER_ENVIRONMENT_SETUP"

STAGE=""
INVOCATION_ID=""
ARGS_BASE64=""
LAUNCH_URL=""
LAUNCH_SHA256=""
BOOTSTRAP_STEP_ID=""
DETACHED_BOOTSTRAP="${CLAWEVOLVE_DETACHED_BOOTSTRAP:-false}"
COMMAND_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage) STAGE="${2:-}"; shift 2 ;;
    --invocation-id) INVOCATION_ID="${2:-}"; shift 2 ;;
    --args-base64) ARGS_BASE64="${2:-}"; shift 2 ;;
    --launch-url) LAUNCH_URL="${2:-}"; shift 2 ;;
    --launch-sha256) LAUNCH_SHA256="${2:-}"; shift 2 ;;
    --bootstrap-step-id) BOOTSTRAP_STEP_ID="${2:-}"; shift 2 ;;
    *) printf '{"ok":false,"error":"unknown argument"}\n' >&2; exit 2 ;;
  esac
done

[[ "$DETACHED_BOOTSTRAP" == "true" || "$DETACHED_BOOTSTRAP" == "false" ]] || {
  printf '{"ok":false,"error":"invalid detached bootstrap mode"}\n' >&2
  exit 2
}

if [[ -n "$LAUNCH_URL" || -n "$LAUNCH_SHA256" ]]; then
  if [[ -z "$LAUNCH_URL" || -z "$LAUNCH_SHA256" || -n "$STAGE" || -n "$INVOCATION_ID" || -n "$ARGS_BASE64" ]]; then
    printf '{"ok":false,"error":"frozen launch cannot be combined with inline arguments"}\n' >&2
    exit 2
  fi
  exec python3 "$SCRIPT_DIR/clawevolve_runner_launch.py" "$LAUNCH_URL" "$LAUNCH_SHA256"
fi

[[ "$STAGE" =~ ^[a-z][a-z0-9-]{0,63}$ ]] || {
  printf '{"ok":false,"error":"invalid stage"}\n' >&2
  exit 2
}

ARGS_PARTS_FILE="$(mktemp /tmp/clawevolve-args.XXXXXX)"
if ! python3 - "$ARGS_BASE64" > "$ARGS_PARTS_FILE" <<'PY'
import base64
import shlex
import sys

try:
    raw = base64.b64decode(sys.argv[1], validate=True).decode("utf-8")
    parts = shlex.split(raw)
except Exception as exc:
    print(f"invalid args payload: {exc}", file=sys.stderr)
    raise SystemExit(2)
for part in parts:
    if "\x00" in part or "\n" in part or "\r" in part:
        print("invalid stage argument", file=sys.stderr)
        raise SystemExit(2)
    sys.stdout.buffer.write(part.encode("utf-8") + b"\0")
PY
then
  rm -f "$ARGS_PARTS_FILE"
  printf '{"ok":false,"error":"invalid args payload"}\n' >&2
  exit 2
fi
while IFS= read -r -d '' part; do
  COMMAND_ARGS+=("$part")
done < "$ARGS_PARTS_FILE"
rm -f "$ARGS_PARTS_FILE"

TASK_ID=""
STEP_ID=""
ROUND=""
DEBUG_MODE=0
DEBUG_SEEN=0
RUNTIME_MAINTENANCE="${CLAWEVOLVE_RUNTIME_MAINTENANCE:-true}"
CLAWWEB_URL_VALUE=""
CLAWWEB_URL_SEEN=0
STAGE_ACTION_SEEN=0
FORWARD_ARGS=()
for ((i=0; i<${#COMMAND_ARGS[@]}; i++)); do
  arg="${COMMAND_ARGS[$i]}"
  key="$arg"
  value=""
  if [[ "$key" == --*=* ]]; then
    value="${key#*=}"
    key="${key%%=*}"
  elif [[ "$key" == --* && $((i + 1)) -lt ${#COMMAND_ARGS[@]} && "${COMMAND_ARGS[$((i + 1))]}" != --* ]]; then
    value="${COMMAND_ARGS[$((i + 1))]}"
  fi
  case "$key" in
    --action)
      if [[ ( "$STAGE" != "stage-execute" && "$STAGE" != "clawevolve-stage" ) \
        || "$STAGE_ACTION_SEEN" != "0" \
        || ( "$value" != "execute" && "$value" != "prepare" && "$value" != "finalize" ) ]]; then
        printf '{"ok":false,"error":"action is managed by the stage runner"}\n' >&2
        exit 2
      fi
      STAGE_ACTION_SEEN=1
      [[ "$arg" == --*=* ]] || i=$((i + 1))
      FORWARD_ARGS+=("--action" "$value")
      continue
      ;;
    --debug)
      (( DEBUG_SEEN == 0 )) || {
        printf '{"ok":false,"error":"debug may only be specified once"}\n' >&2
        exit 2
      }
      DEBUG_SEEN=1
      [[ "$value" == "true" || "$value" == "false" ]] || {
        printf '{"ok":false,"error":"debug must be true or false"}\n' >&2
        exit 2
      }
      if [[ "$value" == "true" ]]; then DEBUG_MODE=1; else DEBUG_MODE=0; fi
      if [[ "$arg" != --*=* ]]; then i=$((i + 1)); fi
      continue
      ;;
    --clawweb-url)
      (( CLAWWEB_URL_SEEN == 0 )) || {
        printf '{"ok":false,"error":"clawweb-url may only be specified once"}\n' >&2
        exit 2
      }
      CLAWWEB_URL_SEEN=1
      CLAWWEB_URL_VALUE="$value"
      [[ "$arg" == --*=* ]] || i=$((i + 1))
      FORWARD_ARGS+=("--clawweb-url" "$value")
      continue
      ;;
    --task-id) TASK_ID="$value" ;;
    --step-id) STEP_ID="$value" ;;
    --round) ROUND="$value" ;;
  esac
  FORWARD_ARGS+=("$arg")
done
COMMAND_ARGS=()
if (( ${#FORWARD_ARGS[@]} > 0 )); then
  COMMAND_ARGS=("${FORWARD_ARGS[@]}")
fi

[[ "$RUNTIME_MAINTENANCE" == "true" || "$RUNTIME_MAINTENANCE" == "false" ]] || {
  printf '{"ok":false,"error":"CLAWEVOLVE_RUNTIME_MAINTENANCE must be true or false"}\n' >&2
  exit 2
}

if (( CLAWWEB_URL_SEEN )); then
  CLAWWEB_URL_VALUE="$(python3 - "$CLAWWEB_URL_VALUE" <<'PY'
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
    raise SystemExit("invalid clawweb-url")
print(raw)
PY
)" || {
    printf '{"ok":false,"error":"invalid clawweb-url"}\n' >&2
    exit 2
  }
  export CLAWEVOLVE_CLAWWEB_URL="$CLAWWEB_URL_VALUE"
  export CLAWWEB_URL="$CLAWWEB_URL_VALUE"
fi

INIT_MODE=0
if [[ "$STAGE" == "init" ]]; then
  INIT_MODE=1
  (( ${#COMMAND_ARGS[@]} == 0 )) || {
    printf '{"ok":false,"error":"init stage does not accept arguments"}\n' >&2
    exit 2
  }
  TASK_ID="INIT"
  STEP_ID="INIT"
fi

validate_id() {
  local value="$2"
  [[ -n "$value" && ${#value} -le 256 && "$value" =~ ^[A-Za-z0-9._:-]+$ ]] || {
    printf '{"ok":false,"error":"invalid %s"}\n' "$1" >&2
    exit 2
  }
}

if (( ! INIT_MODE )); then
  validate_id "task-id" "$TASK_ID"
  validate_id "step-id" "$STEP_ID"
  if [[ -z "$INVOCATION_ID" ]]; then INVOCATION_ID="$STEP_ID"; fi
  validate_id "invocation-id" "$INVOCATION_ID"
else
  INVOCATION_ID="INIT"
fi
if [[ "$DETACHED_BOOTSTRAP" == "true" ]]; then
  validate_id "bootstrap-step-id" "$BOOTSTRAP_STEP_ID"
  [[ "$BOOTSTRAP_STEP_ID" == "$STEP_ID" ]] || {
    printf '{"ok":false,"error":"bootstrap step-id mismatch"}\n' >&2
    exit 2
  }
elif [[ -n "$BOOTSTRAP_STEP_ID" ]]; then
  printf '{"ok":false,"error":"bootstrap-step-id is internal"}\n' >&2
  exit 2
fi
if [[ "$STAGE" == "optimize" ]]; then
  [[ "$ROUND" =~ ^[0-9]+$ ]] && (( ROUND >= 1 && ROUND <= 100 )) || {
    printf '{"ok":false,"error":"invalid round"}\n' >&2
    exit 2
  }
fi
if [[ "$STAGE" == "runtime-cleanup" ]]; then
  RUNTIME_MAINTENANCE="false"
fi
if [[ "$STAGE" == "stop" ]]; then
  STATE_DIR="${OPENCLAW_WORKSPACE}/clawevolve_results/${TASK_ID}/runner_state/${STEP_ID}"
  PID_FILE="${STATE_DIR}/pid"
  PGID_FILE="${STATE_DIR}/pgid"
  PID="$(tr -cd '0-9' < "$PID_FILE" 2>/dev/null || true)"
  PGID="$(tr -cd '0-9' < "$PGID_FILE" 2>/dev/null || true)"
  [[ -n "$PID" ]] || {
    printf '{"ok":true,"status":"already_stopped","task_id":"%s","step_id":"%s"}\n' "$TASK_ID" "$STEP_ID"
    exit 0
  }
  if ! kill -0 "$PID" 2>/dev/null; then
    printf '{"ok":true,"status":"already_stopped","pid":%s,"task_id":"%s","step_id":"%s"}\n' "$PID" "$TASK_ID" "$STEP_ID"
    exit 0
  fi
  [[ -r "/proc/$PID/cmdline" ]] && tr '\0' ' ' < "/proc/$PID/cmdline" | grep -Fq -- "$STEP_ID" || {
    printf '{"ok":false,"error":"runner pid identity mismatch","pid":%s}\n' "$PID" >&2
    exit 1
  }
  [[ -n "$PGID" ]] || PGID="$PID"
  kill -TERM -- "-$PGID" 2>/dev/null || kill -TERM "$PID" 2>/dev/null || true
  for _ in {1..20}; do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.1
  done
  if kill -0 "$PID" 2>/dev/null; then
    kill -KILL -- "-$PGID" 2>/dev/null || kill -KILL "$PID" 2>/dev/null || true
  fi
  printf '{"ok":true,"status":"stopped","pid":%s,"pgid":%s,"task_id":"%s","step_id":"%s"}\n' "$PID" "$PGID" "$TASK_ID" "$STEP_ID"
  exit 0
fi

if (( INIT_MODE )); then
  STATE_DIR="${OPENCLAW_WORKSPACE}/clawevolve_results/runner_init"
  INVOCATION_STATE_DIR="$STATE_DIR"
else
  STATE_DIR="${OPENCLAW_WORKSPACE}/clawevolve_results/${TASK_ID}/runner_state/${STEP_ID}"
  INVOCATION_STATE_DIR="${STATE_DIR}/invocations/${INVOCATION_ID}"
fi
LOCK_DIR="${INVOCATION_STATE_DIR}.start.lock"
SKILL_EXTRACT_DIR=""

cleanup() {
  local exit_status=$?
  local cleanup_lock="${LOCK_DIR}.cleanup.$$"
  set +e
  if [[ -n "$SKILL_EXTRACT_DIR" && -d "$SKILL_EXTRACT_DIR" ]]; then
    rm -rf "$SKILL_EXTRACT_DIR" 2>/dev/null || true
  fi
  if [[ -f "$LOCK_DIR/owner_pid" ]] && [[ "$(cat "$LOCK_DIR/owner_pid" 2>/dev/null || true)" == "$$" ]]; then
    if mv "$LOCK_DIR" "$cleanup_lock" 2>/dev/null; then
      rm -rf "$cleanup_lock" 2>/dev/null || true
    fi
  fi
  report_bootstrap_exit "$exit_status"
  return "$exit_status"
}
trap cleanup EXIT

mkdir -p "$(dirname "$INVOCATION_STATE_DIR")"
if [[ "$DETACHED_BOOTSTRAP" == "true" ]]; then
  # The synchronous request process creates the invocation lock, then hands
  # ownership to this detached process before acknowledging BaaS.
  for _ in {1..100}; do
    [[ "$(cat "$LOCK_DIR/owner_pid" 2>/dev/null || true)" == "$$" ]] && break
    sleep 0.01
  done
  [[ "$(cat "$LOCK_DIR/owner_pid" 2>/dev/null || true)" == "$$" ]] || {
    printf '{"ok":false,"error":"detached bootstrap lock handoff failed"}\n' >&2
    exit 1
  }
elif ! mkdir "$LOCK_DIR" 2>/dev/null; then
  LOCK_PID=""
  if [[ -f "$LOCK_DIR/owner_pid" ]]; then
    LOCK_PID="$(tr -cd '0-9' < "$LOCK_DIR/owner_pid")"
  fi
  if [[ -z "$LOCK_PID" ]]; then
    LOCK_MTIME="$(stat -c '%Y' "$LOCK_DIR" 2>/dev/null || printf '0')"
    if (( $(date +%s) - LOCK_MTIME < 60 )); then
      printf '{"ok":true,"status":"starting","task_id":"%s","step_id":"%s"}\n' "$TASK_ID" "$STEP_ID"
      exit 0
    fi
  fi
  if [[ -n "$LOCK_PID" ]] && kill -0 "$LOCK_PID" 2>/dev/null; then
    LOCK_OWNER_MATCH=0
    if (( INIT_MODE )); then
      # init owns a global installation lock. Once its recorded PID is alive,
      # never steal that lock merely because /proc is unavailable or a wrapper
      # changed the visible command line.
      LOCK_OWNER_MATCH=1
    elif [[ -r "/proc/$LOCK_PID/cmdline" ]] \
      && tr '\0' ' ' < "/proc/$LOCK_PID/cmdline" | grep -Fq -- "$STEP_ID"; then
      LOCK_OWNER_MATCH=1
    fi
    if (( LOCK_OWNER_MATCH )); then
      printf '{"ok":true,"status":"starting","pid":%s,"task_id":"%s","step_id":"%s"}\n' "$LOCK_PID" "$TASK_ID" "$STEP_ID"
      exit 0
    fi
  fi
  STALE_LOCK="${LOCK_DIR}.stale.$$"
  if mv "$LOCK_DIR" "$STALE_LOCK" 2>/dev/null; then
    rm -rf "$STALE_LOCK" 2>/dev/null || true
  fi
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '{"ok":true,"status":"starting","task_id":"%s","step_id":"%s"}\n' "$TASK_ID" "$STEP_ID"
    exit 0
  fi
fi
printf '%s\n' "$$" > "$LOCK_DIR/owner_pid"
mkdir -p "$STATE_DIR" "$INVOCATION_STATE_DIR"

PID_FILE="${INVOCATION_STATE_DIR}/pid"
PGID_FILE="${INVOCATION_STATE_DIR}/pgid"
LAUNCHED_FILE="${INVOCATION_STATE_DIR}/launched"
CURRENT_PID_FILE="${STATE_DIR}/pid"
CURRENT_PGID_FILE="${STATE_DIR}/pgid"
LOG_FILE="${STATE_DIR}/run.log"
log_line() {
  local timestamp
  timestamp="$(date '+%Y-%m-%dT%H:%M:%S%z')"
  printf '%s %s\n' "$timestamp" "$*" >> "$LOG_FILE"
}

# Return success only when the installed fixed-format version is strictly newer
# than the packaged version for the same component. Versions that do not match
# <component>-YYYYMMDD-vN are deliberately not ordered and therefore cannot
# suppress a valid release package update.
version_is_newer() {
  local installed="$1" packaged="$2" component="$3"
  local installed_suffix packaged_suffix
  local installed_date installed_revision packaged_date packaged_revision
  [[ "$installed" == "${component}-"* && "$packaged" == "${component}-"* ]] || return 1
  installed_suffix="${installed#"${component}-"}"
  packaged_suffix="${packaged#"${component}-"}"
  if [[ "$installed_suffix" =~ ^([0-9]{8})-v([0-9]+)$ ]]; then
    installed_date="${BASH_REMATCH[1]}"
    installed_revision="${BASH_REMATCH[2]}"
  else
    return 1
  fi
  if [[ "$packaged_suffix" =~ ^([0-9]{8})-v([0-9]+)$ ]]; then
    packaged_date="${BASH_REMATCH[1]}"
    packaged_revision="${BASH_REMATCH[2]}"
  else
    return 1
  fi
  (( 10#$installed_date > 10#$packaged_date \
    || (10#$installed_date == 10#$packaged_date \
      && 10#$installed_revision > 10#$packaged_revision) ))
}

CLAWEVOLVE_SKILLS_ROOT="${SKILL_BASE_DIR:-${OPENCLAW_WORKSPACE}/clawevolve-skills}"
[[ "$CLAWEVOLVE_SKILLS_ROOT" == /* && "$CLAWEVOLVE_SKILLS_ROOT" != "/" ]] || {
  printf 'SKILL_BASE_DIR must be an absolute non-root path\n' >&2
  exit 2
}
LEGACY_SKILLS_ROOT="${OPENCLAW_WORKSPACE}/skills"
LEGACY_SKILLS_LOCAL_ROOT="${LEGACY_SKILLS_ROOT}/skills-local"
export OPENCLAW_WORKSPACE
export SKILL_BASE_DIR="$CLAWEVOLVE_SKILLS_ROOT"

other_evolve_runner_is_active() {
  local results_root="${OPENCLAW_WORKSPACE}/clawevolve_results"
  local pid_file pid cmdline
  [[ -d "$results_root" ]] || return 1
  while IFS= read -r -d '' pid_file; do
    pid="$(tr -cd '0-9' < "$pid_file" 2>/dev/null || true)"
    [[ -n "$pid" && "$pid" != "$$" ]] || continue
    kill -0 "$pid" 2>/dev/null || continue
    [[ -r "/proc/$pid/cmdline" ]] || continue
    cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    if [[ "$cmdline" == *"clawevolve_task_launcher.sh"* \
      || "$cmdline" == *"clawevolve_async_runner.sh"* \
      || "$cmdline" == *"clawevolve-workflow"* \
      || "$cmdline" == *"clawevolve-diagnose"* \
      || "$cmdline" == *"clawevolve-hardening"* \
      || "$cmdline" == *"clawevolve-plan"* ]]; then
      return 0
    fi
  done < <(find "$results_root" -path '*/runner_state/*/pid' -type f -print0 2>/dev/null)
  return 1
}

legacy_release_entry_name() {
  local basename="$1" candidate="$1"
  if [[ "$candidate" =~ ^\.([A-Za-z0-9._-]+)\.(incoming|backup)\..+$ ]]; then
    candidate="${BASH_REMATCH[1]}"
  fi
  [[ "$candidate" =~ ^(clawevolve-|clawbench-|ocb-)[A-Za-z0-9._-]*$ ]] || return 1
  printf '%s\n' "$candidate"
}

cleanup_legacy_release_skills() {
  local legacy_cleanup_root stale entry basename managed_name link target quarantine marker_remaining=0
  if other_evolve_runner_is_active; then
    log_line "legacy skill cleanup deferred: another ClawEvolve runner is active"
    return 0
  fi
  legacy_cleanup_root="${CLAWEVOLVE_SKILLS_ROOT}/.legacy-cleanup"
  mkdir -p "$legacy_cleanup_root"
  # Retire only our version-marked orchestration Skill after its code runtime
  # is installed. Unmanaged user Skills are outside this migration's ownership.
  entry="${CLAWEVOLVE_SKILLS_ROOT}/clawevolve-stage"
  if [[ -f "${CLAWEVOLVE_SKILLS_ROOT}/platform/clawevolve_runtime/runner.py" \
    && -d "$entry" && ! -L "$entry" && -f "$entry/.clawevolve-version" ]]; then
    quarantine="${legacy_cleanup_root}/clawevolve-stage.$$.$RANDOM"
    mv "$entry" "$quarantine" || return 1
  fi
  while IFS= read -r -d '' stale; do
    rm -rf "$stale" 2>> "$LOG_FILE" || log_line "legacy cleanup retry deferred: ${stale}"
  done < <(find "$legacy_cleanup_root" -mindepth 1 -maxdepth 1 -print0 2>/dev/null)
  while IFS= read -r -d '' entry; do
    basename="${entry##*/}"
    [[ "$basename" == ".clawevolve-release-version" ]] && continue
    managed_name="$(legacy_release_entry_name "$basename" 2>/dev/null || true)"
    [[ -n "$managed_name" ]] || continue
    if [[ -L "$entry" || ! -d "$entry" || ! -f "$entry/.clawevolve-version" ]]; then
      marker_remaining=1
      log_line "legacy skill cleanup skipped (ownership not proven): ${entry}"
      continue
    fi
    link="${LEGACY_SKILLS_ROOT}/${managed_name}"
    if [[ -L "$link" ]]; then
      target="$(readlink "$link" 2>/dev/null || true)"
      if [[ "$target" == "skills-local/${managed_name}" ]]; then
        rm -f "$link" 2>> "$LOG_FILE" || log_line "legacy skill link cleanup warning: ${link}"
      fi
    fi
    quarantine="${legacy_cleanup_root}/${basename}.$$.$RANDOM"
    if mv "$entry" "$quarantine" 2>> "$LOG_FILE"; then
      if ! rm -rf "$quarantine" 2>> "$LOG_FILE"; then
        log_line "legacy skill cleanup deferred (file still busy): ${quarantine}"
      fi
    else
      marker_remaining=1
      log_line "legacy skill cleanup warning: unable to move ${entry}"
    fi
  done < <(find "$LEGACY_SKILLS_LOCAL_ROOT" -mindepth 1 -maxdepth 1 -print0 2>/dev/null)
  while IFS= read -r -d '' entry; do
    basename="${entry##*/}"
    managed_name="$(legacy_release_entry_name "$basename" 2>/dev/null || true)"
    [[ -n "$managed_name" && -d "$entry" && ! -L "$entry" && -f "$entry/.clawevolve-version" ]] || continue
    quarantine="${legacy_cleanup_root}/${basename}.$$.$RANDOM"
    if mv "$entry" "$quarantine" 2>> "$LOG_FILE"; then
      rm -rf "$quarantine" 2>> "$LOG_FILE" || log_line "legacy top-level backup cleanup deferred: ${quarantine}"
    else
      marker_remaining=1
      log_line "legacy top-level backup cleanup warning: unable to move ${entry}"
    fi
  done < <(find "$LEGACY_SKILLS_ROOT" -mindepth 1 -maxdepth 1 -name '.*.backup.*' -print0 2>/dev/null)
  if (( marker_remaining == 0 )); then
    rm -f "${LEGACY_SKILLS_LOCAL_ROOT}/.clawevolve-release-version" 2>> "$LOG_FILE" || true
  fi
}

sync_skills() {
  # The runner and its release manifest/archive are deployed as one
  # self-contained unit. Only the release source changes between pre/prod;
  # the copied private runtime layout below remains environment-neutral.
  local release_root="$SCRIPT_DIR"
  local archive=""
  local release_file="${release_root}/RELEASE_VERSION"
  local runtime_root="$CLAWEVOLVE_SKILLS_ROOT"
  local installed_release_file="${runtime_root}/.clawevolve-release-version"
  local format_version release_version archive_file archive_sha256 installed_release release_marker_tmp release_healthy
  local record name manifest_skill_version packaged_digest source_dir installed_path incoming backup
  [[ -f "$release_file" ]] || { printf 'release manifest not found: %s\n' "$release_file" >&2; return 1; }
  format_version="$(awk -F '\t' '$1 == "format_version" { print $2; exit }' "$release_file")"
  release_version="$(awk -F '\t' '$1 == "release_version" { print $2; exit }' "$release_file")"
  archive_file="$(awk -F '\t' '$1 == "archive_file" { print $2; exit }' "$release_file")"
  archive_sha256="$(awk -F '\t' '$1 == "archive_sha256" { print $2; exit }' "$release_file")"
  [[ "$format_version" == "1" \
    && -n "$release_version" && ${#release_version} -le 128 \
    && "$release_version" =~ ^[A-Za-z0-9._-]+$ \
    && ${#archive_file} -le 151 \
    && "$archive_file" =~ ^clawevolve-skills-[A-Za-z0-9._-]+\.tar$ \
    && "$archive_sha256" =~ ^[a-f0-9]{64}$ ]] || {
    printf 'invalid RELEASE_VERSION manifest\n' >&2
    return 1
  }
  archive="${release_root}/${archive_file}"
  log_line "skill release source: ${release_root}, release=${release_version}"
  mkdir -p "$runtime_root"
  installed_release=""
  if [[ -f "$installed_release_file" ]]; then
    installed_release="$(tr -d '[:space:]' < "$installed_release_file")"
  fi
  if [[ "$installed_release" == "$release_version" ]]; then
    release_healthy=1
    while IFS=$'\t' read -r record name manifest_skill_version packaged_digest; do
      [[ "$record" == "skill" || "$record" == "runtime" ]] || continue
      if [[ ( "$record" == "skill" && ! -f "$runtime_root/$name/SKILL.md" ) \
        || ( "$record" == "runtime" && ! -f "$runtime_root/$name/clawevolve_runtime/runner.py" ) ]]; then
        release_healthy=0
        log_line "skill release repair required: release=${release_version} missing_or_invalid=${name}"
        break
      fi
    done < "$release_file"
    if (( release_healthy )); then
      log_line "skill release unchanged and healthy: ${release_version}"
      cleanup_legacy_release_skills
      return 0
    fi
  fi
  if version_is_newer "$installed_release" "$release_version" "clawevolve"; then
    log_line "skill release downgrade skipped: installed=${installed_release} package=${release_version}"
    cleanup_legacy_release_skills
    return 0
  fi
  [[ -f "$archive" ]] || { printf 'skill package not found: %s\n' "$archive" >&2; return 1; }
  if command -v sha256sum >/dev/null 2>&1; then
    actual_archive_sha256="$(sha256sum "$archive" | awk '{print $1}')"
  else
    actual_archive_sha256="$(shasum -a 256 "$archive" | awk '{print $1}')"
  fi
  [[ "$actual_archive_sha256" == "$archive_sha256" ]] || {
    printf 'skill package checksum mismatch for release %s\n' "$release_version" >&2
    return 1
  }
  if tar -tf "$archive" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
    printf 'unsafe path found in skill package\n' >&2
    return 1
  fi
  SKILL_EXTRACT_DIR="$(mktemp -d /tmp/clawevolve-skills.XXXXXX)"
  tar -C "$SKILL_EXTRACT_DIR" -xf "$archive"
  chmod -R u+rwX "$SKILL_EXTRACT_DIR/skills"
  while IFS=$'\t' read -r record name manifest_skill_version packaged_digest; do
    [[ "$record" == "skill" || "$record" == "runtime" ]] || continue
    [[ "$name" =~ ^[A-Za-z0-9._-]+$ \
      && -n "$manifest_skill_version" && ${#manifest_skill_version} -le 128 \
      && "$manifest_skill_version" =~ ^[A-Za-z0-9._-]+$ \
      && ( -z "$packaged_digest" || "$packaged_digest" =~ ^[a-f0-9]{64}$ ) ]] || {
      printf 'invalid skill manifest entry\n' >&2; return 1;
    }
    source_dir="$SKILL_EXTRACT_DIR/skills/$name"
    installed_path="$runtime_root/$name"
    if [[ "$record" == "skill" ]]; then
      [[ -f "$source_dir/SKILL.md" ]] || { printf 'skill missing from package: %s\n' "$name" >&2; return 1; }
    else
      [[ "$name" == "platform" && -f "$source_dir/clawevolve_runtime/runner.py" ]] || {
        printf 'platform runtime missing from package: %s\n' "$name" >&2; return 1;
      }
    fi
    incoming="$runtime_root/.${name}.incoming.$$"
    backup="$runtime_root/.${name}.backup.$$"
    # These names include this process PID and normally cannot exist. A stale
    # NFS .nfs handle must not make a new installation fail before it starts;
    # move on with a distinct suffix if a previous path is still busy.
    if [[ -e "$incoming" || -e "$backup" ]]; then
      install_suffix="$$.$(date +%s%N)"
      incoming="$runtime_root/.${name}.incoming.${install_suffix}"
      backup="$runtime_root/.${name}.backup.${install_suffix}"
    fi
    cp -a "$source_dir" "$incoming"
    if [[ -e "$runtime_root/$name" ]]; then mv "$runtime_root/$name" "$backup"; fi
    if ! mv "$incoming" "$runtime_root/$name"; then
      [[ -e "$backup" ]] && mv "$backup" "$runtime_root/$name"
      return 1
    fi
    if ! rm -rf "$backup" 2>> "$LOG_FILE"; then
      log_line "skill backup cleanup deferred (file still busy): ${backup}"
    fi
    log_line "skill updated: ${name} release=${release_version}"
  done < "$release_file"
  release_marker_tmp="${installed_release_file}.tmp.$$"
  printf '%s\n' "$release_version" > "$release_marker_tmp"
  mv "$release_marker_tmp" "$installed_release_file"
  log_line "skill release updated: ${installed_release:-none} -> ${release_version}"
  rm -rf "$SKILL_EXTRACT_DIR"
  SKILL_EXTRACT_DIR=""
  cleanup_legacy_release_skills
}

sync_debug_skills() {
  local release_file="${SCRIPT_DIR}/RELEASE_VERSION"
  local record name packaged_version packaged_digest source_dir incoming backup copied=0
  mkdir -p "$CLAWEVOLVE_SKILLS_ROOT"
  [[ -f "$release_file" ]] || {
    printf 'release manifest not found for debug migration: %s\n' "$release_file" >&2
    return 1
  }
  while IFS=$'\t' read -r record name packaged_version packaged_digest; do
    [[ "$record" == "skill" || "$record" == "runtime" ]] || continue
    [[ "$name" =~ ^[A-Za-z0-9._-]+$ ]] || continue
    source_dir="$LEGACY_SKILLS_LOCAL_ROOT/$name"
    local entrypoint="SKILL.md"
    if [[ "$record" == "runtime" ]]; then
      [[ "$name" == "platform" ]] || { printf 'unknown platform runtime: %s\n' "$name" >&2; return 1; }
      entrypoint="clawevolve_runtime/runner.py"
    fi
    if [[ ! -f "$source_dir/$entrypoint" ]]; then
      [[ -f "$CLAWEVOLVE_SKILLS_ROOT/$name/$entrypoint" ]] && continue
      if [[ "$record" == "runtime" ]]; then
        printf 'platform runtime is not installed for debug execution\n' >&2
        return 1
      fi
      log_line "debug skill unavailable in private and legacy roots: ${name}"
      continue
    fi
    incoming="$CLAWEVOLVE_SKILLS_ROOT/.${name}.incoming.$$"
    backup="$CLAWEVOLVE_SKILLS_ROOT/.${name}.backup.$$"
    cp -a "$source_dir" "$incoming"
    if [[ -e "$CLAWEVOLVE_SKILLS_ROOT/$name" ]]; then mv "$CLAWEVOLVE_SKILLS_ROOT/$name" "$backup"; fi
    if ! mv "$incoming" "$CLAWEVOLVE_SKILLS_ROOT/$name"; then
      [[ -e "$backup" ]] && mv "$backup" "$CLAWEVOLVE_SKILLS_ROOT/$name"
      return 1
    fi
    rm -rf "$backup" 2>> "$LOG_FILE" || true
    copied=$((copied + 1))
  done < "$release_file"
  log_line "debug skill migration completed: copied=${copied} root=${CLAWEVOLVE_SKILLS_ROOT}"
  cleanup_legacy_release_skills
}

if [[ "$DETACHED_BOOTSTRAP" == "false" && "$STAGE" != "runtime-cleanup" && "$STAGE" != "init" ]]; then
  # A first install or release upgrade may take minutes on the Bot workspace.
  # Acknowledge the synchronous BaaS execute-command request first, then let
  # this invocation's detached child synchronize Skills and launch the Stage.
  RUNNER_PATH="${SCRIPT_DIR}/clawevolve_async_runner.sh"
  TASK_LAUNCHER="${SCRIPT_DIR}/clawevolve_task_launcher.sh"
  if ! RUNNER_SYNTAX_ERROR="$(bash -n "$RUNNER_PATH" 2>&1)"; then
    log_line "detached bootstrap syntax validation failed: ${RUNNER_SYNTAX_ERROR}"
    printf '{"ok":false,"code":"RUNNER_INVALID","error":"runner syntax validation failed","task_id":"%s","step_id":"%s"}\n' \
      "$TASK_ID" "$STEP_ID" >&2
    exit 1
  fi
  if [[ ! -x "$TASK_LAUNCHER" ]] || ! LAUNCHER_SYNTAX_ERROR="$(bash -n "$TASK_LAUNCHER" 2>&1)"; then
    log_line "task launcher validation failed before detached bootstrap: ${LAUNCHER_SYNTAX_ERROR:-not executable}"
    printf '{"ok":false,"code":"TASK_LAUNCHER_INVALID","error":"task launcher validation failed","task_id":"%s","step_id":"%s"}\n' \
      "$TASK_ID" "$STEP_ID" >&2
    exit 1
  fi
  BOOTSTRAP_COMMAND=(
    env CLAWEVOLVE_DETACHED_BOOTSTRAP=true
    "CLAWEVOLVE_BOOTSTRAP_TASK_ID=$TASK_ID"
    "CLAWEVOLVE_BOOTSTRAP_STEP_ID=$STEP_ID"
    "CLAWEVOLVE_BOOTSTRAP_URL=$CLAWWEB_URL_VALUE"
    bash "$RUNNER_PATH"
    --stage "$STAGE"
    --invocation-id "$INVOCATION_ID"
    --args-base64 "$ARGS_BASE64"
    --bootstrap-step-id "$STEP_ID"
  )
  if command -v setsid >/dev/null 2>&1; then
    setsid nohup "${BOOTSTRAP_COMMAND[@]}" >> "$LOG_FILE" 2>&1 < /dev/null &
  else
    nohup "${BOOTSTRAP_COMMAND[@]}" >> "$LOG_FILE" 2>&1 < /dev/null &
  fi
  PID=$!
  printf '%s\n' "$PID" > "$LOCK_DIR/owner_pid"
  printf '%s\n' "$PID" > "$PID_FILE"
  printf '%s\n' "$PID" > "$PGID_FILE"
  printf '%s\n' "$PID" > "$CURRENT_PID_FILE"
  printf '%s\n' "$PID" > "$CURRENT_PGID_FILE"
  log_line "detached bootstrap started: pid=${PID} stage=${STAGE} invocation=${INVOCATION_ID}"
  printf '{"ok":true,"status":"started","pid":%s,"task_id":"%s","step_id":"%s","invocation_id":"%s"}\n' \
    "$PID" "$TASK_ID" "$STEP_ID" "$INVOCATION_ID"
  exit 0
fi

if [[ "$STAGE" == "runtime-cleanup" ]]; then
  log_line "runtime cleanup: skip skill synchronization"
else
  mkdir -p "$CLAWEVOLVE_SKILLS_ROOT"
  # Keep the same lock inode across invocations. Deleting a directory lock can
  # fail on the mounted workspace; fcntl also works without a flock CLI on macOS.
  # The shell retains fd 9 after the Python helper exits and throughout sync.
  exec 9>"${CLAWEVOLVE_SKILLS_ROOT}/.sync.lock"
  python3 -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX)'
  if (( DEBUG_MODE )); then
    sync_debug_skills
  else
    RELEASE_BEFORE_SYNC=""
    RELEASE_MARKER="${CLAWEVOLVE_SKILLS_ROOT}/.clawevolve-release-version"
    if [[ -f "$RELEASE_MARKER" ]]; then
      RELEASE_BEFORE_SYNC="$(tr -d '[:space:]' < "$RELEASE_MARKER")"
    fi
    sync_skills
  fi
  python3 -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_UN)'
  exec 9>&-
fi

# Environment adaptation, runtime cleanup, and Gateway restart run inside the
# detached task launcher. The synchronous BaaS execute-command request must
# return before restarting OpenClaw, otherwise the ARCA facade loses its own
# control channel and reports COMMAND_FAILED even though the shell was valid.

if [[ -f "$LAUNCHED_FILE" ]]; then
  PID="$(tr -cd '0-9' < "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
    printf '{"ok":true,"status":"running","pid":"%s","task_id":"%s","step_id":"%s"}\n' "$PID" "$TASK_ID" "$STEP_ID"
    exit 0
  fi
  log_line "stale launcher state: launched marker exists but pid is not running pid=${PID:-missing}"
  printf '{"ok":false,"code":"RUNNER_LAUNCH_STALE","error":"launcher exited after dispatch; retry this invocation with a new delivery identity","pid":"%s","task_id":"%s","step_id":"%s","invocation_id":"%s"}\n' \
    "$PID" "$TASK_ID" "$STEP_ID" "$INVOCATION_ID" >&2
  exit 1
fi

if (( INIT_MODE )); then
  INSTALLED_RELEASE_FILE="${CLAWEVOLVE_SKILLS_ROOT}/.clawevolve-release-version"
  INSTALLED_RELEASE=""
  if [[ -f "$INSTALLED_RELEASE_FILE" ]]; then
    INSTALLED_RELEASE="$(tr -d '[:space:]' < "$INSTALLED_RELEASE_FILE")"
  fi
  INIT_RESULT="installed"
  if [[ "${RELEASE_BEFORE_SYNC:-}" == "$INSTALLED_RELEASE" ]]; then INIT_RESULT="unchanged"; fi
  log_line "init stage completed: result=${INIT_RESULT} release=${INSTALLED_RELEASE:-unknown}"
  printf '{"ok":true,"status":"initialized","result":"%s","release_version":"%s"}\n' "$INIT_RESULT" "$INSTALLED_RELEASE"
  exit 0
fi

resolve_skill_file() {
  local skill_name="$1"
  local relative_path="$2"
  local candidate
  candidate="${CLAWEVOLVE_SKILLS_ROOT}/${skill_name}/${relative_path}"
  if [[ -f "$candidate" && -r "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi
  return 1
}

RUN_CWD=""
case "$STAGE" in
  stage-execute|clawevolve-stage)
    # clawevolve-stage is only a historical command alias, never a Skill.
    STAGE_RUNNER="${CLAWEVOLVE_SKILLS_ROOT}/platform/clawevolve_runtime/runner.py"
    [[ -f "$STAGE_RUNNER" && -r "$STAGE_RUNNER" ]] || {
      printf '{"ok":false,"error":"platform Stage execution runtime is not installed"}\n' >&2
      exit 1
    }
    RUN_CWD="$OPENCLAW_WORKSPACE"
    EXEC_COMMAND=(python3 -u -B "$STAGE_RUNNER" "${COMMAND_ARGS[@]}")
    ;;
  clawevolve-diagnose)
    if ! STAGE_RUNNER="$(resolve_skill_file "clawevolve-diagnose" "scripts/run.sh")"; then
      printf '{"ok":false,"error":"clawevolve-diagnose scripts/run.sh not found or unreadable in installed or release skill"}\n' >&2
      exit 1
    fi
    RUN_CWD="$OPENCLAW_WORKSPACE"
    EXEC_COMMAND=(bash "$STAGE_RUNNER" "${COMMAND_ARGS[@]}")
    ;;
  clawevolve-hardening)
    if ! STAGE_RUNNER="$(resolve_skill_file "clawevolve-hardening" "scripts/run.sh")"; then
      printf '{"ok":false,"error":"clawevolve-hardening scripts/run.sh not found or unreadable in installed or release skill"}\n' >&2
      exit 1
    fi
    RUN_CWD="$OPENCLAW_WORKSPACE"
    EXEC_COMMAND=(bash "$STAGE_RUNNER" "${COMMAND_ARGS[@]}")
    ;;
  clawevolve-plan)
    if ! STAGE_RUNNER="$(resolve_skill_file "clawevolve-plan" "scripts/run.sh")"; then
      printf '{"ok":false,"error":"clawevolve-plan scripts/run.sh not found or unreadable in installed or release skill"}\n' >&2
      exit 1
    fi
    RUN_CWD="$OPENCLAW_WORKSPACE"
    EXEC_COMMAND=(bash "$STAGE_RUNNER" "${COMMAND_ARGS[@]}")
    ;;
  bench-plan|optimize)
    if ! STAGE_RUNNER="$(resolve_skill_file "clawevolve-workflow" "scripts/run.py")"; then
      printf '{"ok":false,"error":"clawevolve-workflow scripts/run.py not found or unreadable"}\n' >&2
      exit 1
    fi
    EXEC_COMMAND=(python3 -u -B "$STAGE_RUNNER" --stage "$STAGE" "${COMMAND_ARGS[@]}")
    ;;
  clawevolve-bench)
    if ! STAGE_RUNNER="$(resolve_skill_file "clawevolve-workflow" "scripts/handlers/clawevolve_bench_run.py")"; then
      printf '{"ok":false,"error":"clawevolve_bench_run.py not found or unreadable"}\n' >&2
      exit 1
    fi
    EXEC_COMMAND=(python3 -u -B "$STAGE_RUNNER" "${COMMAND_ARGS[@]}")
    ;;
  clawevolve-pack)
    if ! STAGE_RUNNER="$(resolve_skill_file "clawevolve-workflow" "scripts/handlers/clawevolve_pack_run.py")"; then
      printf '{"ok":false,"error":"clawevolve_pack_run.py not found or unreadable"}\n' >&2
      exit 1
    fi
    EXEC_COMMAND=(python3 -u -B "$STAGE_RUNNER" "${COMMAND_ARGS[@]}")
    ;;
  runtime-cleanup)
    STAGE_RUNNER="${SCRIPT_DIR}/clawevolve_runtime_cleanup.py"
    [[ -f "$STAGE_RUNNER" && -r "$STAGE_RUNNER" ]] || {
      printf '{"ok":false,"error":"runtime cleanup handler not found or unreadable"}\n' >&2
      exit 1
    }
    RUN_CWD="$OPENCLAW_WORKSPACE"
    EXEC_COMMAND=(python3 -u -B "$STAGE_RUNNER" "${COMMAND_ARGS[@]}")
    ;;
  clawevolve-*|clawbench)
    if ! STAGE_RUNNER="$(resolve_skill_file "clawevolve-workflow" "scripts/handlers/${STAGE}.sh")"; then
      printf '{"ok":false,"error":"stage handler not installed","stage":"%s"}\n' "$STAGE" >&2
      exit 1
    fi
    EXEC_COMMAND=(bash "$STAGE_RUNNER" "${COMMAND_ARGS[@]}")
    ;;
  *)
    printf '{"ok":false,"error":"unsupported stage","stage":"%s"}\n' "$STAGE" >&2
    exit 2
    ;;
esac

log_line "dispatch stage: ${STAGE}"
LOG_COMMAND_ARGS=()
REDACT_NEXT_API_KEY=0
for arg in "${EXEC_COMMAND[@]}"; do
  if (( REDACT_NEXT_API_KEY )); then
    LOG_COMMAND_ARGS+=("***")
    REDACT_NEXT_API_KEY=0
  elif [[ "$arg" == "--api-key" ]]; then
    LOG_COMMAND_ARGS+=("$arg")
    REDACT_NEXT_API_KEY=1
  elif [[ "$arg" == --api-key=* ]]; then
    LOG_COMMAND_ARGS+=("--api-key=***")
  else
    LOG_COMMAND_ARGS+=("$arg")
  fi
done
printf -v EXEC_COMMAND_LOG '%q ' "${LOG_COMMAND_ARGS[@]}"
log_line "launch handler argv: ${EXEC_COMMAND_LOG% }"
# Do not use process substitution here. Its long-lived logger process keeps the
# execute-command process tree (and potentially its capture descriptors) alive,
# so BaaS can report COMMAND_FAILED even though the handler started correctly.
# Redirect the detached handler straight to the step log so the runner can
# return immediately and close every descriptor owned by the request.
TASK_LAUNCHER="${SCRIPT_DIR}/clawevolve_task_launcher.sh"
[[ -x "$TASK_LAUNCHER" ]] || {
  printf '{"ok":false,"error":"clawevolve task launcher not found or not executable"}\n' >&2
  exit 1
}
if ! LAUNCHER_SYNTAX_ERROR="$(bash -n "$TASK_LAUNCHER" 2>&1)"; then
  log_line "task launcher syntax validation failed: ${LAUNCHER_SYNTAX_ERROR}"
  printf '{"ok":false,"code":"TASK_LAUNCHER_INVALID","error":"task launcher syntax validation failed","task_id":"%s","step_id":"%s"}\n' \
    "$TASK_ID" "$STEP_ID" >&2
  exit 1
fi
LAUNCH_COMMAND=(
  bash "$TASK_LAUNCHER"
  --task-id "$TASK_ID"
  --step-id "$STEP_ID"
  --log-file "$LOG_FILE"
  --runtime-maintenance "$RUNTIME_MAINTENANCE"
)
if [[ "$STAGE" == "runtime-cleanup" ]]; then
  LAUNCH_COMMAND+=(--preflight-active-evolve-guard true)
  LAUNCH_COMMAND+=(--refresh-gateway-before-handler true)
fi
if [[ -n "$RUN_CWD" ]]; then
  LAUNCH_COMMAND+=(--run-cwd "$RUN_CWD")
fi
if [[ -n "$CLAWWEB_URL_VALUE" ]]; then
  LAUNCH_COMMAND+=(--clawweb-url "$CLAWWEB_URL_VALUE")
fi
LAUNCH_COMMAND+=(-- "${EXEC_COMMAND[@]}")
if command -v setsid >/dev/null 2>&1; then
  setsid nohup "${LAUNCH_COMMAND[@]}" >> "$LOG_FILE" 2>&1 < /dev/null &
else
  nohup "${LAUNCH_COMMAND[@]}" >> "$LOG_FILE" 2>&1 < /dev/null &
fi
PID=$!
printf '%s\n' "$PID" > "$PID_FILE"
printf '%s\n' "$PID" > "$PGID_FILE"
printf '%s\n' "$PID" > "$CURRENT_PID_FILE"
printf '%s\n' "$PID" > "$CURRENT_PGID_FILE"
touch "$LAUNCHED_FILE"

printf '{"ok":true,"status":"started","pid":%s,"task_id":"%s","step_id":"%s","invocation_id":"%s"}\n' "$PID" "$TASK_ID" "$STEP_ID" "$INVOCATION_ID"
