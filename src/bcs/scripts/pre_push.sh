#!/usr/bin/env bash
# BCS pre-push gates: unit tests (nextest, fast-fail) + e2e line coverage
# (instrumented bcs + 5 bots, --bcs-min 30) — run in parallel with fail-fast.
#
# Invoked by scripts/ci/pre_push.sh when the push range touches src/bcs/.
# Mirrors ci_test.sh's --base/--head contract and OCB_PRE_PUSH_ENABLE_BCS*
# opt-out flags.
#
# Usage:
#   bash src/bcs/scripts/pre_push.sh --base <base> --head <head>
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
bcs_dir="$repo_root/src/bcs"

base=""
head="HEAD"
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --base)
      if [[ "$#" -lt 2 ]]; then
        echo "Error: --base requires a value" >&2
        exit 2
      fi
      base="$2"; shift 2 ;;
    --head)
      if [[ "$#" -lt 2 ]]; then
        echo "Error: --head requires a value" >&2
        exit 2
      fi
      head="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

run_required() {
  echo ""
  echo "== required: $* =="
  "$@"
}

# ----------------------------------------------------------------------------
# Parallel-gate helpers (Bash 3.2 compatible: no associative arrays, no wait -n)
# ----------------------------------------------------------------------------
_PREPUSH_LOG_DIR="$repo_root/scripts/.dependencies/logs/prepush"

# _kill_tree <pid> [signal] — recursively terminate a process and all its
# descendants. Portable (no `setsid`); macOS ships pgrep/pkill in /usr/bin.
# Descendants first so a parent's exit traps/cleanup don't re-spawn them.
_kill_tree() {
  local pid="$1" sig="${2:-TERM}"
  local child
  while IFS= read -r child; do
    [ -n "$child" ] && _kill_tree "$child" "$sig"
  done < <(pgrep -P "$pid" 2>/dev/null || true)
  kill -"$sig" "$pid" 2>/dev/null || true
}

# _exit_rc <exitfile> -> the exit code recorded in <exitfile>, sanitized:
# strip whitespace and default to 124 (timeout/abnormal) when missing/empty.
# An empty or non-numeric value would otherwise break `[[ "$rc" -ne 0 ]]`.
_exit_rc() {
  local rc
  rc=$(cat "$1" 2>/dev/null || true)
  rc="${rc//[[:space:]]/}"
  echo "${rc:-124}"
}

# _launch_tagged <tag> <exitfile> <cmd...>
# Runs cmd in a background subshell; combined stdout+stderr is teed through a
# FIFO to a line-prefixer that tags every line "[<tag>] ..." for interleaved-
# but-tidy live output, and a copy is written to <tag>.log. The command's exit
# code is written to <exitfile> so the scheduler can detect completion. Sets
# globals _TASK_READER (prefixer pid) and _TASK_PID (producer pid); use
# _kill_tree on the latter to tear the whole tree down on fail-fast.
_launch_tagged() {
  local tag="$1" exitfile="$2"; shift 2
  local fifo="$_PREPUSH_LOG_DIR/pipe.$tag"
  local logfile="$_PREPUSH_LOG_DIR/$tag.log"
  rm -f "$fifo" "$exitfile"
  mkfifo "$fifo"
  # `[[ -n "$line" ]]` lets the loop capture a final line lacking a trailing
  # newline (read returns non-zero on EOF even when it read a partial line),
  # so the last tagged line is never dropped.
  ( while IFS= read -r line || [[ -n "$line" ]]; do
      printf '%s\n' "[$tag] $line"
    done < "$fifo" ) &
  _TASK_READER=$!
  # Pass fifo/logfile/exitfile as positional args instead of interpolating them
  # into the bash -c string; the nested-quoting form is fragile for paths with
  # special characters.
  bash -c '
    set +em
    fifo="$1"; logfile="$2"; exitfile="$3"; shift 3
    "$@" 2>&1 | tee "$fifo" >"$logfile"
    printf "%s\n" "${PIPESTATUS[0]}" > "$exitfile"
  ' _ "$fifo" "$logfile" "$exitfile" "$@" < /dev/null &
  _TASK_PID=$!
  disown "$_TASK_PID" 2>/dev/null || true
}

# Run the unit and e2e-coverage gates in parallel with fail-fast. Both gates
# share no resources (isolated CARGO_TARGET_DIR, ephemeral unit-test ports vs
# fixed 21000/3000x in e2e, temp vs shared data dirs), so concurrent execution
# is safe. Output is line-tagged via FIFO so streams interleave cleanly; a
# per-gate PASSED/FAIL/KILLED summary block is printed at the end. The first
# gate to exit non-zero terminates the other's whole process tree and blocks
# the push. Returns non-zero on any failure.
bcs_parallel_gates() {
  local base="$1" head="$2"
  mkdir -p "$_PREPUSH_LOG_DIR"
  local unit_log="$_PREPUSH_LOG_DIR/unit.log"
  local e2e_log="$_PREPUSH_LOG_DIR/e2e.log"
  local unit_exit="$_PREPUSH_LOG_DIR/unit.exit"
  local e2e_exit="$_PREPUSH_LOG_DIR/e2e.exit"
  rm -f "$unit_log" "$e2e_log"

  echo ""
  echo "== launching bcs gates in parallel =="
  printf '  unit: src/bcs/scripts/ci_test.sh --fast-fail   (log: %s)\n' "$unit_log"
  printf '  e2e:  src/bcs/scripts/e2e_coverage.sh --bcs-min 30 --force-rebuild   (log: %s)\n' "$e2e_log"
  echo ""

  _launch_tagged unit "$unit_exit" \
    "$bcs_dir/scripts/ci_test.sh" --base "$base" --head "$head" --fast-fail
  local u_reader=$_TASK_READER u_pid=$_TASK_PID
  _launch_tagged e2e "$e2e_exit" \
    "$bcs_dir/scripts/e2e_coverage.sh" --bcs-min 30 --force-rebuild
  local e_reader=$_TASK_READER e_pid=$_TASK_PID

  # Wait for the first gate to finish (exitfile written) or die abnormally.
  local first_tag="" first_rc="124"
  while true; do
    if [[ -f "$unit_exit" ]]; then first_tag=unit; first_rc=$(_exit_rc "$unit_exit"); break; fi
    if [[ -f "$e2e_exit" ]]; then first_tag=e2e; first_rc=$(_exit_rc "$e2e_exit"); break; fi
    # Abnormal: process gone without writing exit (e.g. SIGKILL).
    if ! kill -0 "$u_pid" 2>/dev/null; then first_tag=unit; first_rc=124; break; fi
    if ! kill -0 "$e_pid" 2>/dev/null; then first_tag=e2e; first_rc=124; break; fi
    sleep 1
  done

  local unit_state e2e_state unit_rc e2e_rc
  if [[ "$first_rc" -ne 0 ]]; then
    # Fail-fast: terminate the other gate's whole process tree.
    if [[ "$first_tag" == unit ]]; then
      unit_rc="$first_rc"; unit_state="FAIL"
      _kill_tree "$e_pid" TERM
      e2e_rc=124; e2e_state="KILLED"
    else
      e2e_rc="$first_rc"; e2e_state="FAIL"
      _kill_tree "$u_pid" TERM
      unit_rc=124; unit_state="KILLED"
    fi
    # Give whoever is being torn down a moment to flush log lines, then reap.
    sleep 1
    wait "$u_reader" 2>/dev/null || true
    wait "$e_reader" 2>/dev/null || true
  else
    # First gate passed; wait for the second.
    if [[ "$first_tag" == unit ]]; then
      unit_rc="$first_rc"; unit_state="PASS"
      while ! [[ -f "$e2e_exit" ]] && kill -0 "$e_pid" 2>/dev/null; do sleep 1; done
      e2e_rc=$(_exit_rc "$e2e_exit")
    else
      e2e_rc="$first_rc"; e2e_state="PASS"
      while ! [[ -f "$unit_exit" ]] && kill -0 "$u_pid" 2>/dev/null; do sleep 1; done
      unit_rc=$(_exit_rc "$unit_exit")
    fi
    [[ "$unit_rc" -eq 0 ]] && unit_state="PASS" || unit_state="FAIL"
    [[ "$e2e_rc" -eq 0 ]] && e2e_state="PASS" || e2e_state="FAIL"
    wait "$u_reader" 2>/dev/null || true
    wait "$e_reader" 2>/dev/null || true
  fi

  echo ""
  echo "================== BCS pre-push gates: summary =================="
  printf '  unit (ci_test.sh)        %s  (rc=%s)\n' "$unit_state" "$unit_rc"
  printf '  e2e  (e2e_coverage.sh)   %s  (rc=%s)\n' "$e2e_state" "$e2e_rc"
  printf 'logs:\n  unit: %s\n  e2e:  %s\n' "$unit_log" "$e2e_log"

  if [[ "$unit_rc" -ne 0 || "$e2e_rc" -ne 0 ]]; then
    local fail_tag fail_log fail_rc
    if [[ "$e2e_rc" -ne 0 ]]; then fail_tag=e2e; fail_log="$e2e_log"; fail_rc="$e2e_rc"
    else fail_tag=unit; fail_log="$unit_log"; fail_rc="$unit_rc"; fi
    echo ""
    echo "Result: push BLOCKED — failing gate: $fail_tag (rc=$fail_rc)."
    echo "--- last 40 lines of $fail_tag log ($fail_log) ---"
    tail -n 40 "$fail_log" 2>/dev/null || true
    echo "--- end ---"
    return 1
  fi
  echo "Result: PASSED — both gates green."
  return 0
}

# ----------------------------------------------------------------------------
# Dispatch
# ----------------------------------------------------------------------------
unit_on=1; e2e_on=1
[[ "${OCB_PRE_PUSH_ENABLE_BCS:-1}" == "1" ]] || unit_on=0
[[ "${OCB_PRE_PUSH_ENABLE_BCS_E2E:-1}" == "1" ]] || e2e_on=0

if [[ $unit_on -eq 0 && $e2e_on -eq 0 ]]; then
  echo "bcs changes detected; BCS gates skipped (OCB_PRE_PUSH_ENABLE_BCS=0 OCB_PRE_PUSH_ENABLE_BCS_E2E=0)"
elif [[ $unit_on -eq 1 && $e2e_on -eq 1 ]]; then
  bcs_parallel_gates "$base" "$head"
elif [[ $unit_on -eq 1 ]]; then
  run_required "$bcs_dir/scripts/ci_test.sh" --base "$base" --head "$head" --fast-fail
else
  run_required "$bcs_dir/scripts/e2e_coverage.sh" --bcs-min 30 --force-rebuild
fi