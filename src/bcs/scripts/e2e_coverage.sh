#!/usr/bin/env bash
set -euo pipefail

# BCS e2e line-coverage orchestration script (bcs-only, ported and trimmed
# from ocb).
# Flow: singlebox --with-bcs-coverage start bcs_bots -> e2e.sh -> SIGTERM bcs
#       (flush profraw) -> singlebox stop bcs_bots -> cargo llvm-cov report
#       --cobertura.
#
# Gate semantics:
#   - e2e is always a 100% gate (no switch): any e2e failure makes the script
#     exit with the non-zero e2e exit code.
#   - --bcs-min defaults to 0: do not gate coverage, just collect; pass a value
#     to additionally gate on line coverage.
#   - The two are independent: e2e failure does not block aggregation
#     (cobertura.xml is still produced), and low coverage does not block the
#     exit code (default 0).
#
# Usage:
#   bash src/bcs/scripts/e2e_coverage.sh              # full flow
#   bash src/bcs/scripts/e2e_coverage.sh --skip-start # instrumented bcs already running; run e2e + stop + aggregate only
#   bash src/bcs/scripts/e2e_coverage.sh --no-stop    # do not stop bcs after running (debug; no aggregation)
#   bash src/bcs/scripts/e2e_coverage.sh --bcs-min 30 # additionally gate coverage >= 30%
#   bash src/bcs/scripts/e2e_coverage.sh --force-rebuild # force-rebuild instrumented bcs (ignore cache)
#
# Coverage scope: bcs server (crate bcs) only; excludes the 5 bots / Python.

repo_root="$(git rev-parse --show-toplevel)"
bcs_dir="$repo_root/src/bcs"
cov_dir="$bcs_dir/target/cov-e2e"
out_xml="$cov_dir/cobertura.xml"
report_file="$cov_dir/coverage.txt"
bcs_port="${BCS_PORT:-21000}"
bcs_min="${BCS_E2E_COVERAGE_MIN:-0}"

skip_start=0
no_stop=0
force_rebuild=0
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --skip-start)   skip_start=1; shift ;;
    --no-stop)      no_stop=1;    shift ;;
    --bcs-min)
      if [[ "$#" -lt 2 ]]; then
        echo "Error: --bcs-min requires a value" >&2
        exit 2
      fi
      bcs_min="$2"; shift 2 ;;
    --force-rebuild) force_rebuild=1; shift ;;
    -h|--help)      sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# With --force-rebuild, rebuild the instrumented bcs even when a cached binary
# exists. The pre-push gate uses this so an up-to-date binary reflecting the
# pushed source is exercised; cargo build is incremental so a no-op rebuild
# only re-links.
if [[ "$force_rebuild" -eq 1 ]]; then
  export BCS_COVERAGE_FORCE_REBUILD=1
fi

# Trap fallback: SIGTERM bcs on early exit (set -e, etc.) so profraw flushes as
# best it can. Install only when --no-stop is not set (avoid the contradiction
# of --no-stop still killing bcs).
cleanup_bcs() {
  local pids
  pids=$(lsof -tiTCP:"$bcs_port" -sTCP:LISTEN 2>/dev/null || true)
  [[ -n "$pids" ]] && echo "$pids" | xargs kill -TERM 2>/dev/null || true
}
if [[ "$no_stop" -eq 0 ]]; then
  trap cleanup_bcs EXIT
fi

# The 5 bot ports, matching the BOT1..BOT5_PORT defaults in scripts/modules/bcs.sh.
bot_ports=(30001 30011 30021 30031 30041)

# Preflight reclamation: the coverage bcs must be the instrumented build, and
# bots_start hard-errors if any of 30001-30041 are occupied. So before launch,
# if 21000 (bcs) or any bot port is already in use (leftover from a prior run /
# a bcs_bots started elsewhere / full stack / non-singlebox processes), kill
# the occupying bcs/openclaw processes with a warning, then start the
# instrumented stack. Do NOT call `singlebox stop` here (it would also tear down
# the frontend, which e2e does not need).
# Skipped under --skip-start (caller takes responsibility for the instrumented bcs).
preflight_reclaim_ports() {
  local busy=() p
  lsof -tiTCP:"$bcs_port" -sTCP:LISTEN >/dev/null 2>&1 && busy+=("$bcs_port")
  for p in "${bot_ports[@]}"; do
    lsof -tiTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1 && busy+=("$p")
  done
  [ ${#busy[@]} -eq 0 ] && return 0

  echo "WARN: ports busy (${busy[*]}); killing existing bcs/openclaw processes" >&2
  local port pids
  # SIGTERM first (openclaw exits cleanly, avoiding profile residue); SIGKILL
  # anything still holding a port after 2s.
  for port in "${busy[@]}"; do
    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    [ -n "$pids" ] && echo "$pids" | xargs kill 2>/dev/null || true
  done
  sleep 2
  for port in "${busy[@]}"; do
    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    [ -n "$pids" ] && echo "$pids" | xargs kill -9 2>/dev/null || true
  done
  sleep 1
}

# 1. Start the instrumented service + 5 bots (unless --skip-start).
#    Uses --standalone so bot profiles live under <checkout>/.standalone-openclaw
#    (per-checkout isolation) instead of the shared ~/.openclaw-* that --local
#    writes. Without this, running in a second checkout/worktree collides with
#    the first's ~/.openclaw-* stamps ("profile exists but does not match this
#    singlebox local stack"). e2e.sh needs the 5 demo bots onboarded first
#    (UUIDs resolved by name), so start bcs_bots not just bcs; otherwise
#    GET /bots is empty and every resolution fails. bcs_bots = bcs + bots,
#    no frontend (e2e needs none).
if [[ "$skip_start" -eq 0 ]]; then
  preflight_reclaim_ports
  "$repo_root/scripts/singlebox.sh" --standalone --with-bcs-coverage start bcs_bots
fi

# 2. Run e2e (curl hits :21000, exercising the instrumented bcs; profraw is
#    appended continuously). Do not let e2e failure abort aggregation: the
#    coverage report is produced even on failure (a failing run is still useful
#    to see what was covered).
e2e_status=0
bash "$bcs_dir/scripts/e2e-test/e2e.sh" || e2e_status=$?
if [[ "$e2e_status" -ne 0 ]]; then
  echo "WARN: e2e exited with $e2e_status; continuing to flush profraw and aggregate coverage." >&2
fi

# 3 & 4 & 5: stop bcs (SIGTERM flush) -> stop bots -> aggregate.
if [[ "$no_stop" -eq 0 ]]; then
  # 3. SIGTERM bcs, wait for graceful shutdown (max 30s), fall back to SIGKILL
  #    on timeout (WARN, not fail). lsof may return multiple PIDs (e.g. socket
  #    sharing or child processes), so signal via xargs and check liveness per
  #    PID rather than quoting the multi-line string to a single kill.
  bcs_pids=$(lsof -tiTCP:"$bcs_port" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "$bcs_pids" ]]; then
    echo "--- SIGTERM bcs (pids=$(echo "$bcs_pids" | tr '\n' ' ')) to flush profraw ---"
    echo "$bcs_pids" | xargs kill -TERM 2>/dev/null || true
    still=""
    for i in $(seq 1 30); do
      still=""
      while IFS= read -r p; do
        [ -n "$p" ] && kill -0 "$p" 2>/dev/null && still="$still $p"
      done <<< "$bcs_pids"
      [ -z "$still" ] && break
      sleep 1
    done
    still=""
    while IFS= read -r p; do
      [ -n "$p" ] && kill -0 "$p" 2>/dev/null && still="$still $p"
    done <<< "$bcs_pids"
    if [[ -n "$still" ]]; then
      echo "WARN: bcs did not exit in 30s (pids:$still), SIGKILL (profraw may be incomplete)" >&2
      echo "$still" | xargs kill -9 2>/dev/null || true
    else
      echo "--- bcs exited gracefully ---"
    fi
  else
    echo "WARN: no bcs listening on :$bcs_port; skip SIGTERM" >&2
  fi

  # 4. Stop bots (bcs already stopped via SIGTERM; bots are non-Rust, so
  #    SIGKILL is harmless). Stop bcs_bots only, not the frontend (e2e needs no
  #    frontend; do not kill the user's dev server). Pass --standalone so singlebox
  #    resolves the per-checkout standalone pid/profile paths started in step 1.
  "$repo_root/scripts/singlebox.sh" --standalone stop bcs_bots

  # 5. Aggregate cobertura + text table.
  # Two report runs: one with --cobertura --output-path to produce the XML
  # (--fail-under-lines enforces the threshold; --output-path consumes stdout);
  # one plain-text pass into coverage.txt (per-file table for the record and for
  # grepping TOTAL).
  # --package bcs: coverage scope is the bcs server only. Onboarding invokes the
  # instrumented bcs-cli (BCS_CLI_BIN points at the instrumented build), whose
  # profraw lands in the same dir; -p bcs excludes bcs-cli lines from the report,
  # upholding the "bcs server only" intent.
  echo "--- aggregating coverage ---"
  set +e
  (
    cd "$bcs_dir"
    export CARGO_TARGET_DIR="$cov_dir"
    cargo llvm-cov report --package bcs --cobertura \
      --output-path "$out_xml" \
      --fail-under-lines="$bcs_min" > /dev/null 2>&1
    cobertura_status=$?
    cargo llvm-cov report --package bcs > "$report_file" 2>&1
    exit "$cobertura_status"
  )
  cov_status=$?
  set -e

  echo ""
  echo "✓ Coverage report: $out_xml"
  echo "--- summary ---"
  grep '^TOTAL' "$report_file" || tail -20 "$report_file"

  if [[ "$cov_status" -ne 0 ]]; then
    echo "bcs coverage failed (threshold --bcs-min=$bcs_min not met); full report: $report_file" >&2
    tail -40 "$report_file" >&2
    # Coverage threshold failed: if e2e fully passed, exit with the coverage
    # failure code.
    [[ "$e2e_status" -eq 0 ]] && exit "$cov_status"
  fi
fi

# Post-aggregation cleanup of profraw. The instrumented cargo build redirects
# build-time profraw (build scripts / proc-macros, worthless to the report) to
# src/bcs/target/tmp via LLVM_PROFILE_FILE (set in prepare_bcs_coverage_bin),
# so they never touch the source tree. As a belt-and-suspenders guard, also
# remove any default-*.profraw that an older code path may still write into the
# source tree. Runtime profraw under cov_dir is already merged into
# cobertura.xml/coverage.txt; clear it too. Skipped under --no-stop (debugging
# may still want them).
if [[ "$no_stop" -eq 0 ]]; then
  removed=0
  while IFS= read -r -d '' f; do rm -f "$f"; removed=$((removed + 1)); done \
    < <(find "$bcs_dir" -name 'default_*.profraw' -not -path '*/target/*' -print0 2>/dev/null)
  if [[ "$removed" -gt 0 ]]; then
    echo "Cleaned up $removed stray profraw file(s) from bcs source tree"
  fi
  # cov_dir contains the llvm-cov-target/ subdirectory where runtime profraw
  # (e.g. bcs-*.profraw) lands; recursively delete all profraw under cov_dir
  # (already merged into cobertura.xml/coverage.txt). Also clear build-time
  # profraw redirected to src/bcs/target/tmp so it doesn't accumulate across runs.
  find "$cov_dir" -name '*.profraw' -delete 2>/dev/null || true
  find "$bcs_dir/target/tmp" -name '*.profraw' -delete 2>/dev/null || true
fi

# The e2e exit code is the script's final exit code (e2e is always a 100% gate).
exit "$e2e_status"