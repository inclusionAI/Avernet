#!/usr/bin/env bash
set -euo pipefail

# BCS e2e line-coverage orchestration script (bcs-only, ported and trimmed
# from ocb).
# Flow: singlebox --with-bcs-coverage start bcs_bots -> e2e.sh -> SIGTERM bcs
#       (flush profraw) -> singlebox stop bcs_bots -> cargo llvm-cov report
#       --cobertura / text / --summary-only --json -> e2e_cov_gate.py.
#
# Gate semantics:
#   - e2e is always a 100% gate (no switch): any e2e failure makes the script
#     exit with the non-zero e2e exit code.
#   - Line and method coverage thresholds default to 0 (collect + report only).
#     Pass --bcs-line-min / --bcs-method-min to gate them independently.
#     --bcs-min remains as a compatibility shortcut that sets both thresholds.
#     Region coverage is reported but NOT gated. Emits GitHub
#     ::notice::/::error:: annotations (local: OK/FAIL) for each metric.
#   - Adapter HTTP endpoint coverage is always gated at 100%.
#   - bcs-cli leaf-command coverage is always gated at 100%.
#   - The gates are independent and can fail the run: e2e failure takes
#     precedence, followed by endpoint/CLI coverage, then line/method coverage.
#
# Usage:
#   bash src/bcs/scripts/e2e_coverage.sh              # full flow
#   bash src/bcs/scripts/e2e_coverage.sh --skip-start # instrumented bcs already running; run e2e + stop + aggregate only
#   bash src/bcs/scripts/e2e_coverage.sh --no-stop    # do not stop bcs after running (debug; no aggregation)
#   bash src/bcs/scripts/e2e_coverage.sh --bcs-line-min 40 --bcs-method-min 36
#                                                    # gate line >=40%, method >=36%
#   bash src/bcs/scripts/e2e_coverage.sh --force-rebuild # force-rebuild instrumented bcs (ignore cache)
#
# Coverage scope: bcs server (crate bcs) only; excludes the 5 bots / Python.

repo_root="$(git rev-parse --show-toplevel)"
bcs_dir="$repo_root/src/bcs"
cov_dir="$bcs_dir/target/cov-e2e"
out_xml="$cov_dir/cobertura.xml"
report_file="$cov_dir/coverage.txt"
bcs_port="${BCS_PORT:-21000}"
compat_min="${BCS_E2E_COVERAGE_MIN:-0}"
line_min="${BCS_E2E_LINE_MIN:-$compat_min}"
method_min="${BCS_E2E_METHOD_MIN:-$compat_min}"

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
      line_min="$2"; method_min="$2"; shift 2 ;;
    --bcs-line-min)
      if [[ "$#" -lt 2 ]]; then
        echo "Error: --bcs-line-min requires a value" >&2
        exit 2
      fi
      line_min="$2"; shift 2 ;;
    --bcs-method-min)
      if [[ "$#" -lt 2 ]]; then
        echo "Error: --bcs-method-min requires a value" >&2
        exit 2
      fi
      method_min="$2"; shift 2 ;;
    --force-rebuild) force_rebuild=1; shift ;;
    -h|--help)      sed -n '2,30p' "$0"; exit 0 ;;
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
  # Export LLVM_PROFILE_FILE BEFORE singlebox starts the instrumented bcs server
  # (and bcs-cli). prepare_bcs_coverage_bin sets it too, but only inside
  # singlebox's process; the bcs server it launches inherits this one. Without
  # it the instrumented server falls back to the profiler default
  # `default_<hash>_<pid>.profraw` in its own CWD — the repo root, where
  # singlebox is invoked from — leaking profraw there (outside target/, not
  # covered by src/bcs/.gitignore). Pointing it at cov-e2e/llvm-cov-target
  # keeps runtime profraw next to the objects cargo llvm-cov report merges.
  cov_runtime_dir="$bcs_dir/target/cov-e2e/llvm-cov-target"
  export LLVM_PROFILE_FILE="$cov_runtime_dir/bcs-%m-%p.profraw"
  # Enable BCS_DEBUG so bcs's debug_middleware logs every request as
  # `[→BCS] METHOD PATH` to bcs.log (singlebox redirects bcs stderr there). The
  # endpoint-coverage report below diffs those hits against router.rs's full
  # registered endpoint set. Exported here so the bcs server singlebox launches
  # inherits it (bcs-cli spawns under e2e.sh inherit it too, but their requests
  # still hit the running bcs server and are logged server-side — what we want).
  export BCS_DEBUG=true
  # Match start_bcs_binary: BCS_LOG = ${LOG_DIR}/bcs.log where
  # LOG_DIR=${DEP_DIR}/logs and DEP_DIR=<repo>/scripts/.dependencies (DEP_DIR is
  # resolved relative to singlebox's own SCRIPT_DIR = <repo>/scripts, NOT the
  # repo root). singlebox redirects bcs's stderr to it; debug_middleware writes
  # there with BCS_DEBUG=true above. The file is truncated right before e2e runs
  # below so the endpoint report counts only the e2e suite's hits, not singlebox's
  # bot-onboarding setup requests nor a stale log from a prior run.
  bcs_log="$repo_root/scripts/.dependencies/logs/bcs.log"
  "$repo_root/scripts/singlebox.sh" --standalone --with-bcs-coverage start bcs_bots
fi

# Re-export the instrumented binary paths for the e2e.sh child process.
# prepare_bcs_coverage_bin (inside singlebox.sh) exports BCS_BIN / BCS_CLI_BIN,
# but singlebox.sh runs as a child process, so its exports die when it exits —
# e2e.sh (invoked below as another child) never sees them. Without this re-export
# bcs_cli falls back to `cargo run -p bcs-cli`, which fails ("could not find
# Cargo.toml" from the repo root) and breaks every cli case + the cli-driven
# group setups. Locally this is masked because src/bcs/target/debug/bcs-cli
# exists from a prior non-instrumented build; CI has only the instrumented target.
# LLVM_PROFILE_FILE (set above) persists across both children since it is re-set
# here too — belt-and-suspenders for bcs-cli spawns under e2e.sh.
cov_cli_bin="$bcs_dir/target/cov-e2e/llvm-cov-target/debug/bcs-cli"
if [[ -x "$cov_cli_bin" ]]; then
  export BCS_CLI_BIN="$cov_cli_bin"
  export BCS_BIN="$bcs_dir/target/cov-e2e/llvm-cov-target/debug/bcs"
  export LLVM_PROFILE_FILE="$bcs_dir/target/cov-e2e/llvm-cov-target/bcs-%m-%p.profraw"
else
  echo "WARN: instrumented bcs-cli not found at $cov_cli_bin; e2e cli cases will use the src/bcs/target/debug fallback or cargo run." >&2
fi

# 2. Run e2e (curl hits :21000, exercising the instrumented bcs; profraw is
#    appended continuously). Do not let e2e failure abort aggregation: the
#    coverage report is produced even on failure (a failing run is still useful
#    to see what was covered).
e2e_status=0
cov_gate_status=0
endpoint_gate_status=0
cli_gate_status=0
# Baseline the BCS_DEBUG hit log so the endpoint-coverage report (run at
# aggregation time) counts only this e2e suite's requests, not singlebox's
# bot-onboarding setup nor a prior run's stale log. bcs appends to bcs_log, so
# truncating here is safe (it is already running; it keeps its file offset and
# the next line lands at the new EOF). Skipped under --skip-start only if the
# caller also set BCS_LOG to point elsewhere — otherwise reuse the default.
bcs_log="${bcs_log:-$repo_root/scripts/.dependencies/logs/bcs.log}"
: > "$bcs_log" 2>/dev/null || true
cli_coverage_log="$cov_dir/cli_commands.log"
mkdir -p "$cov_dir"
: > "$cli_coverage_log"
export BCS_CLI_COVERAGE_LOG="$cli_coverage_log"
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

  # 5. Aggregate cobertura + text table + JSON summary.
  # Three report passes over the on-disk profraw (no rebuild, ~seconds each):
  #   --cobertura --output-path   -> cobertura.xml (artifact; line/branch rates)
  #   plain text                  -> coverage.txt  (per-file table for the record)
  #   --summary-only --json       -> summary.json  (structured line/function/region
  #                                  totals) consumed by e2e_cov_gate.py below.
  # NOTE: coverage scope here includes bcs-cli profraw (onboarding runs the
  # instrumented bcs-cli). That matches the historical report; the gate keys on
  # overall totals, so it does not change pass/fail semantics.
  summary_json="$cov_dir/summary.json"
  echo "--- aggregating coverage ---"
  set +e
  (
    cd "$bcs_dir"
    export CARGO_TARGET_DIR="$cov_dir"
    cargo llvm-cov report --cobertura --output-path "$out_xml" > /dev/null 2>&1
    cargo llvm-cov report > "$report_file" 2>&1
    cargo llvm-cov report --summary-only --json > "$summary_json" 2>/dev/null
  )
  set -e

  echo ""
  echo "✓ Coverage report: $out_xml"
  echo "--- summary ---"
  grep '^TOTAL' "$report_file" || tail -20 "$report_file"

  # Coverage gate (e2e_cov_gate.py). Replaces the old --fail-under-lines gate:
  #   - Enforces line and method (function) coverage independently; region is
  #     reported but NOT gated (e2e runtime region coverage is low/noisy).
  #   - Emits GitHub ::notice::/::error:: annotations (local: OK/FAIL lines)
  #     for each metric, mirroring cov_gate.py's style.
  #   - Runs regardless of e2e_status so its annotations always surface, and
  #     its exit code is combined with e2e's below — no more swallowed gate
  #     (the old `[[ e2e eq 0 ]] && exit` skipped coverage failure when e2e
  #     itself failed, so coverage thresholds appeared not to take effect).
  # A threshold of 0 is report-only. Pre-push/CI gate line >=40% and method
  # >=36%; --bcs-min remains available to set both to one value.
  if [[ -f "$summary_json" ]]; then
    ( cd "$bcs_dir" && python3 scripts/e2e_cov_gate.py \
        --summary "$summary_json" \
        --line-min "$line_min" \
        --method-min "$method_min" ) || cov_gate_status=$?
  else
    echo "WARN: coverage summary.json not found ($summary_json); skipping coverage gate." >&2
    # Missing summary is itself a gate failure when a threshold was requested:
    # silently passing would let coverage regressions go unnoticed.
    if [[ "$line_min" != "0" || "$method_min" != "0" ]]; then
      cov_gate_status=1
    fi
  fi

  # Adapters endpoint coverage (hard 100% gate): of all HTTP routes
  # registered in bcs-http's router.rs, which does this e2e run exercise? Diff
  # the BCS_DEBUG hit log (collected above) against the parsed endpoint set.
  # See scripts/adapters_endpoint_coverage.py for the over/under-count self-checks.
  endpoint_txt="$cov_dir/endpoint_coverage.txt"
  endpoint_xml="$cov_dir/endpoint_coverage.xml"
  # The script prints only a short summary to stdout (no per-endpoint detail);
  # the full covered/uncovered lists go to the .txt and structured .xml files.
  ( cd "$bcs_dir" && python3 scripts/adapters_endpoint_coverage.py \
      --router crates/adapters/http/bcs-http/src/router.rs \
      --log "$bcs_log" \
      --out-txt "$endpoint_txt" \
      --out-xml "$endpoint_xml" \
      --min 100 ) || endpoint_gate_status=$?

  # bcs-cli command coverage (hard 100% gate): discover the complete leaf
  # command tree recursively from Clap --help and compare it with command paths
  # recorded by the shared E2E bcs_cli wrapper. Help pseudo-commands and aliases
  # are intentionally excluded by the reporter.
  cli_coverage_txt="$cov_dir/cli_command_coverage.txt"
  if [[ -x "$cov_cli_bin" ]]; then
    ( cd "$bcs_dir" && python3 scripts/cli_command_coverage.py \
        --cli "$cov_cli_bin" \
        --log "$cli_coverage_log" \
        --out-txt "$cli_coverage_txt" \
        --min 100 ) || cli_gate_status=$?
  else
    echo "FAIL: bcs-cli command coverage binary not found at $cov_cli_bin" >&2
    cli_gate_status=1
  fi
fi

# Post-aggregation cleanup of profraw. The instrumented cargo build redirects
# build-time profraw (build scripts / proc-macros, worthless to the report) to
# src/bcs/target/tmp via LLVM_PROFILE_FILE, so they never touch the source tree.
# As a belt-and-suspenders guard, also remove any default-*.profraw that an
# older code path (or a run before the LLVM_PROFILE_FILE pre-export fix) may
# write into the source tree OR the repo root — start_bcs_bots.sh launches the
# instrumented bcs server in singlebox's CWD (repo root when invoked from there);
# if LLVM_PROFILE_FILE wasn't propagated (historical bug), the server wrote
# default_*.profraw at the repo root, which src/bcs/.gitignore does NOT cover.
# Runtime profraw under cov_dir is already merged into cobertura.xml/coverage.txt;
# clear it too. Skipped under --no-stop (debugging may still want them).
if [[ "$no_stop" -eq 0 ]]; then
  removed=0
  while IFS= read -r -d '' f; do rm -f "$f"; removed=$((removed + 1)); done \
    < <(find "$bcs_dir" -name 'default_*.profraw' -not -path '*/target/*' -print0 2>/dev/null)
  # Repo-root sweep: only inside the checkout (NOT above repo_root), and skip
  # anything under a target/ dir (legit coverage artifacts).
  while IFS= read -r -d '' f; do rm -f "$f"; removed=$((removed + 1)); done \
    < <(find "$repo_root" -mindepth 1 -maxdepth 1 -name 'default_*.profraw' -print0 2>/dev/null)
  if [[ "$removed" -gt 0 ]]; then
    echo "Cleaned up $removed stray profraw file(s) from the source tree / repo root"
  fi
  # cov_dir contains the llvm-cov-target/ subdirectory where runtime profraw
  # (e.g. bcs-*.profraw) lands; recursively delete all profraw under cov_dir
  # (already merged into cobertura.xml/coverage.txt). Also clear build-time
  # profraw redirected to src/bcs/target/tmp so it doesn't accumulate across runs.
  find "$cov_dir" -name '*.profraw' -delete 2>/dev/null || true
  find "$bcs_dir/target/tmp" -name '*.profraw' -delete 2>/dev/null || true
fi

# Final exit code: e2e is always a 100% gate; the coverage gate (when a
# a line/method threshold was requested) is an independent gate. e2e failure takes
# precedence (the suite is broken), but a passing e2e with a breached coverage
# threshold must still fail the run — which the old swallowed `[[ e2e eq 0 ]]
# && exit` logic did not guarantee.
if [[ "$e2e_status" -ne 0 ]]; then
  exit "$e2e_status"
fi
if [[ "$endpoint_gate_status" -ne 0 ]]; then
  exit "$endpoint_gate_status"
fi
if [[ "$cli_gate_status" -ne 0 ]]; then
  exit "$cli_gate_status"
fi
exit "$cov_gate_status"
