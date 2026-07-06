#!/usr/bin/env bash
# Run bcs-cli chat repeatedly to observe overlapping task concurrency.

set -euo pipefail

BOT_UUID="test_bot_uuid_001:12345678"
MESSAGE="执行这个脚本： for i in {1..6}; do echo hello; sleep 5; done"
INTERVAL_SECONDS=10
DURATION_SECONDS=600
EXPECTED_RUN_SECONDS=30
CLI_BIN="bcs-cli"
DRY_RUN=false
LOG_DIR=""

ACTIVE_IDS=()
ACTIVE_PIDS=()
SUCCESS_COUNT=0
FAIL_COUNT=0
MAX_OBSERVED_CONCURRENCY=0

usage() {
  cat <<'EOF'
Usage:
  chat-concurrency-loop.sh [options]

Options:
  --bot-uuid UUID             Bot UUID to send the chat message to.
  --message TEXT              Chat message to send.
  --interval-seconds N        Seconds between launches. Default: 10.
  --duration-seconds N        Total launch window. Default: 600.
  --expected-run-seconds N    Expected single run duration for peak estimate. Default: 30.
  --cli PATH                  bcs-cli executable. Default: bcs-cli.
  --log-dir DIR               Directory for per-run logs.
  --dry-run                   Print planned launches without calling bcs-cli or sleeping.
  -h, --help                  Show this help.

Default command:
  bcs-cli chat --bot-uuid "test_bot_uuid_001:12345678" --message "执行这个脚本： for i in {1..6}; do echo hello; sleep 5; done"
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

require_positive_int() {
  local name="$1"
  local value="$2"
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || die "$name must be a positive integer: $value"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bot-uuid)
      [[ $# -ge 2 ]] || die "--bot-uuid requires a value"
      BOT_UUID="$2"
      shift 2
      ;;
    --message)
      [[ $# -ge 2 ]] || die "--message requires a value"
      MESSAGE="$2"
      shift 2
      ;;
    --interval-seconds)
      [[ $# -ge 2 ]] || die "--interval-seconds requires a value"
      INTERVAL_SECONDS="$2"
      shift 2
      ;;
    --duration-seconds)
      [[ $# -ge 2 ]] || die "--duration-seconds requires a value"
      DURATION_SECONDS="$2"
      shift 2
      ;;
    --expected-run-seconds)
      [[ $# -ge 2 ]] || die "--expected-run-seconds requires a value"
      EXPECTED_RUN_SECONDS="$2"
      shift 2
      ;;
    --cli)
      [[ $# -ge 2 ]] || die "--cli requires a value"
      CLI_BIN="$2"
      shift 2
      ;;
    --log-dir)
      [[ $# -ge 2 ]] || die "--log-dir requires a value"
      LOG_DIR="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

require_positive_int "interval-seconds" "$INTERVAL_SECONDS"
require_positive_int "duration-seconds" "$DURATION_SECONDS"
require_positive_int "expected-run-seconds" "$EXPECTED_RUN_SECONDS"

PLANNED_RUNS=$(((DURATION_SECONDS + INTERVAL_SECONDS - 1) / INTERVAL_SECONDS))
THEORETICAL_PEAK=$(((EXPECTED_RUN_SECONDS + INTERVAL_SECONDS - 1) / INTERVAL_SECONDS))

if [[ -z "$LOG_DIR" ]]; then
  LOG_DIR="${TMPDIR:-/tmp}/bcs-chat-loop-$(date '+%Y%m%d-%H%M%S')"
fi
mkdir -p "$LOG_DIR"

if [[ "$DRY_RUN" != true ]] && ! command -v "$CLI_BIN" >/dev/null 2>&1; then
  die "cannot find bcs-cli executable: $CLI_BIN"
fi

timestamp() {
  date '+%Y-%m-%dT%H:%M:%S%z'
}

cleanup_children() {
  if [[ ${#ACTIVE_PIDS[@]} -gt 0 ]]; then
    echo ""
    echo "interrupt: stopping ${#ACTIVE_PIDS[@]} active child process(es)" >&2
    local pid
    for pid in "${ACTIVE_PIDS[@]}"; do
      kill "$pid" 2>/dev/null || true
    done
  fi
}
trap cleanup_children INT TERM

reap_finished() {
  local next_ids=()
  local next_pids=()
  local idx id pid status_file exit_code elapsed ended_at

  for idx in "${!ACTIVE_IDS[@]}"; do
    id="${ACTIVE_IDS[$idx]}"
    pid="${ACTIVE_PIDS[$idx]}"
    status_file="$LOG_DIR/run-${id}.status"

    if [[ -f "$status_file" ]]; then
      read -r exit_code elapsed ended_at < "$status_file" || {
        exit_code=1
        elapsed=unknown
        ended_at="$(timestamp)"
      }

      if [[ "$exit_code" == "0" ]]; then
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
      else
        FAIL_COUNT=$((FAIL_COUNT + 1))
      fi

      printf '[%s] done  %s exit=%s elapsed=%ss log=%s\n' \
        "$id" "$ended_at" "$exit_code" "$elapsed" "$LOG_DIR/run-${id}.log"
      wait "$pid" 2>/dev/null || true
    else
      next_ids+=("$id")
      next_pids+=("$pid")
    fi
  done

  ACTIVE_IDS=()
  ACTIVE_PIDS=()
  if [[ ${#next_ids[@]} -gt 0 ]]; then
    ACTIVE_IDS=("${next_ids[@]}")
    ACTIVE_PIDS=("${next_pids[@]}")
  fi
}

launch_run() {
  local run_number="$1"
  local id log_file status_file started_at start_epoch pid concurrent

  id="$(printf '%03d' "$run_number")"
  log_file="$LOG_DIR/run-${id}.log"
  status_file="$LOG_DIR/run-${id}.status"
  started_at="$(timestamp)"
  start_epoch="$(date '+%s')"

  (
    exit_code=0
    {
      echo "run=$id"
      echo "started_at=$started_at"
      echo "bot_uuid=$BOT_UUID"
      echo "message=$MESSAGE"
      echo "command=$CLI_BIN chat --bot-uuid \"$BOT_UUID\" --message \"$MESSAGE\""
      echo ""

      set +e
      "$CLI_BIN" chat --bot-uuid "$BOT_UUID" --message "$MESSAGE"
      exit_code=$?
      set -e

      ended_epoch="$(date '+%s')"
      elapsed=$((ended_epoch - start_epoch))
      ended_at="$(timestamp)"

      echo ""
      echo "exit_code=$exit_code"
      echo "ended_at=$ended_at"
      echo "elapsed_seconds=$elapsed"
    } > "$log_file" 2>&1

    printf '%s %s %s\n' "$exit_code" "$elapsed" "$ended_at" > "$status_file"
    exit "$exit_code"
  ) &

  pid=$!
  ACTIVE_IDS+=("$id")
  ACTIVE_PIDS+=("$pid")
  concurrent="${#ACTIVE_IDS[@]}"
  if [[ "$concurrent" -gt "$MAX_OBSERVED_CONCURRENCY" ]]; then
    MAX_OBSERVED_CONCURRENCY="$concurrent"
  fi

  printf '[%s] start %s pid=%s concurrent=%s log=%s\n' \
    "$id" "$started_at" "$pid" "$concurrent" "$log_file"
}

echo "== BCS chat concurrency loop =="
echo "dry_run=$DRY_RUN"
echo "bot_uuid=$BOT_UUID"
echo "interval_seconds=$INTERVAL_SECONDS"
echo "duration_seconds=$DURATION_SECONDS"
echo "expected_run_seconds=$EXPECTED_RUN_SECONDS"
echo "planned_runs=$PLANNED_RUNS"
echo "theoretical_peak_concurrency=$THEORETICAL_PEAK"
echo "log_dir=$LOG_DIR"
echo ""

if [[ "$DRY_RUN" == true ]]; then
  for ((run = 1; run <= PLANNED_RUNS; run++)); do
    id="$(printf '%03d' "$run")"
    printf '[%s] start %s dry-run log=%s/run-%s.log\n' \
      "$id" "$(timestamp)" "$LOG_DIR" "$id"
  done
  echo ""
  echo "== Summary =="
  echo "planned_runs=$PLANNED_RUNS"
  echo "success=0"
  echo "failed=0"
  echo "max_observed_concurrency=0"
  echo "log_dir=$LOG_DIR"
  exit 0
fi

BASE_EPOCH="$(date '+%s')"
for ((run = 1; run <= PLANNED_RUNS; run++)); do
  target_epoch=$((BASE_EPOCH + (run - 1) * INTERVAL_SECONDS))
  now_epoch="$(date '+%s')"
  if [[ "$target_epoch" -gt "$now_epoch" ]]; then
    sleep "$((target_epoch - now_epoch))"
  fi

  reap_finished
  launch_run "$run"
done

while [[ ${#ACTIVE_IDS[@]} -gt 0 ]]; do
  sleep 1
  reap_finished
done

echo ""
echo "== Summary =="
echo "planned_runs=$PLANNED_RUNS"
echo "success=$SUCCESS_COUNT"
echo "failed=$FAIL_COUNT"
echo "max_observed_concurrency=$MAX_OBSERVED_CONCURRENCY"
echo "log_dir=$LOG_DIR"
