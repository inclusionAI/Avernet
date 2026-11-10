#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"

export PATH="${HOME}/.cargo/bin:${PATH}"
# BCSFuse currently pins faiss-cpu 1.8.0, whose published wheels stop at
# CPython 3.12. Keep local pre-push aligned with the GitHub workflow instead of
# accidentally selecting a newer host Python during `uv sync`.
export UV_PYTHON="${UV_PYTHON:-3.12}"
export SINGLEBOX_GATEWAY_PRINCIPAL_SIGNING_KEY="${SINGLEBOX_GATEWAY_PRINCIPAL_SIGNING_KEY:-singlebox-gateway-principal-key-not-for-production}"

coverage_root="${SINGLEBOX_COVERAGE_ROOT:-$repo_root/singlebox/.dependencies/coverage/singlebox}"
report_dir="$coverage_root/reports"
mode="${SINGLEBOX_COVERAGE_MODE:-real}"
model_config_mode="${SINGLEBOX_COVERAGE_MODEL_CONFIG_MODE:-mock}"
module_manifest="$script_dir/singlebox_coverage_modules.yaml"
bcs_coverage_dir="$repo_root/apps/bcs/target/cov-e2e"
bcs_line_min="${SINGLEBOX_COVERAGE_BCS_LINE_MIN:-54}"
bcs_method_min="${SINGLEBOX_COVERAGE_BCS_METHOD_MIN:-48}"
coverage_standalone_root=""
coverage_standalone_runtime=""
repair_provider_fixture=""
requested_modules=()
acceptance_targets=()
explicit_acceptance_targets=()
coverage_modules=()
reporter_command=()
phase_timing_file=""
bcs_companion_pid=""
source "$repo_root/apps/bcs/scripts/e2e-test/mock_services.sh"

record_phase_timing() {
  local phase="$1" start_epoch="$2" end_epoch="$3" status="$4"
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$phase" "$start_epoch" "$end_epoch" "$((end_epoch - start_epoch))" "$status" \
    >> "$phase_timing_file"
}

timed_phase() {
  local phase="$1"; shift
  local start_epoch end_epoch status=0
  start_epoch="$(date +%s)"
  echo "[phase] $phase: start"
  "$@" || status=$?
  end_epoch="$(date +%s)"
  record_phase_timing "$phase" "$start_epoch" "$end_epoch" "$status"
  echo "[phase] $phase: exit=$status elapsed=$((end_epoch - start_epoch))s"
  return "$status"
}

start_bcs_companions() {
  mkdir -p "$report_dir/bcs"
  # Production build/start and repair restart have finished; this is the only
  # background Cargo producer. Reporting will not run until it is joined.
  python3 "$repo_root/apps/bcs/scripts/ci/companion_coverage.py" \
    --bcs-dir "$repo_root/apps/bcs" --report-dir "$report_dir/bcs" \
    > "$report_dir/bcs/companions.log" 2>&1 &
  bcs_companion_pid=$!
  echo "[phase] bcs_companions: started (overlapping acceptance/shared stories)"
}

wait_bcs_companions() {
  local status=0
  wait "$bcs_companion_pid" || status=$?
  bcs_companion_pid=""
  cat "$report_dir/bcs/companions.log"
  if [[ "$status" -ne 0 ]]; then
    local log
    for log in companion-build caller oauth; do
      [[ ! -f "$report_dir/bcs/$log.log" ]] || tail -n 100 "$report_dir/bcs/$log.log"
    done
  fi
  return "$status"
}

stop_bcs_companions() {
  if [[ -n "$bcs_companion_pid" ]]; then
    # The Python supervisor owns process-group termination/reaping, including
    # any Rust test servers. Wait for it before touching shared profile files.
    kill -TERM "$bcs_companion_pid" 2>/dev/null || true
    wait "$bcs_companion_pid" 2>/dev/null || true
    bcs_companion_pid=""
  fi
}

if [[ -n "${SINGLEBOX_COVERAGE_MODULE:-}" ]]; then
  requested_modules+=("$SINGLEBOX_COVERAGE_MODULE")
fi
if [[ -n "${SINGLEBOX_COVERAGE_ACCEPTANCE_TARGET:-}" ]]; then
  explicit_acceptance_targets+=("$SINGLEBOX_COVERAGE_ACCEPTANCE_TARGET")
fi

usage() {
  cat <<USAGE
Usage: singlebox/ci/singlebox_coverage.sh [OPTIONS]

Singlebox coverage gate entrypoint used by pre-push and PR CI.

The default mode is real: pre-push starts the local singlebox coverage stack.

Options:
  --coverage-root DIR     Coverage output root, default: $coverage_root
  --mode real             Override SINGLEBOX_COVERAGE_MODE
  --acceptance-target PATH
                          Override manifest targets; may be repeated
  --module NAME           Run and gate one manifest module; may be repeated
                          Default: every enabled module in the manifest
  --bcs-line-min N        BCS runtime line coverage minimum, default: $bcs_line_min
  --bcs-method-min N      BCS runtime method coverage minimum, default: $bcs_method_min
  -h, --help              Show this help

Environment:
  SINGLEBOX_COVERAGE_MODEL_CONFIG_MODE
                          OpenClaw model config mode: mock (default), manual, or home
USAGE
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --coverage-root)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --coverage-root requires an argument" >&2
        exit 2
      fi
      coverage_root="$2"
      report_dir="$coverage_root/reports"
      shift 2
      ;;
    --mode)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --mode requires an argument" >&2
        exit 2
      fi
      mode="$2"
      shift 2
      ;;
    --acceptance-target)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --acceptance-target requires an argument" >&2
        exit 2
      fi
      explicit_acceptance_targets+=("$2")
      shift 2
      ;;
    --module)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --module requires an argument" >&2
        exit 2
      fi
      requested_modules+=("$2")
      shift 2
      ;;
    --bcs-line-min)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --bcs-line-min requires an argument" >&2
        exit 2
      fi
      bcs_line_min="$2"
      shift 2
      ;;
    --bcs-method-min)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --bcs-method-min requires an argument" >&2
        exit 2
      fi
      bcs_method_min="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

# Service launchers embed this root into generated configs and then cd into
# their own module directories. A relative --coverage-root would silently
# point BCS at a nonexistent config dir, so absolutize before any use.
coverage_root="$(mkdir -p "$coverage_root" && cd "$coverage_root" && pwd -P)"
report_dir="$coverage_root/reports"

if [[ ! -s "$module_manifest" ]]; then
  echo "missing singlebox coverage module manifest: $module_manifest" >&2
  exit 2
fi

resolve_reporter_command() {
  if [[ -n "${PYTHON:-}" ]]; then
    reporter_command=("$PYTHON")
  elif [[ -x "$repo_root/apps/backend/.venv/bin/python" ]]; then
    reporter_command=("$repo_root/apps/backend/.venv/bin/python")
  else
    reporter_command=(uv run --project "$repo_root/apps/backend" python)
  fi
}

resolve_coverage_scope() {
  local selection_args=(--manifest "$module_manifest")
  local module_name target modules_output targets_output
  if [[ "${#requested_modules[@]}" -gt 0 ]]; then
    for module_name in "${requested_modules[@]}"; do
      selection_args+=(--module "$module_name")
    done
  fi

  if ! modules_output="$("${reporter_command[@]}" \
    "$script_dir/singlebox_coverage_report.py" \
    "${selection_args[@]}" --list-modules)"; then
    return 2
  fi
  while IFS= read -r module_name; do
    [[ -n "$module_name" ]] && coverage_modules+=("$module_name")
  done <<< "$modules_output"
  if [[ "${#coverage_modules[@]}" -eq 0 ]]; then
    echo "singlebox coverage manifest selected no modules" >&2
    return 2
  fi

  if [[ "${#explicit_acceptance_targets[@]}" -gt 0 ]]; then
    acceptance_targets=("${explicit_acceptance_targets[@]}")
  else
    if ! targets_output="$("${reporter_command[@]}" \
      "$script_dir/singlebox_coverage_report.py" \
      "${selection_args[@]}" --list-acceptance-targets)"; then
      return 2
    fi
    while IFS= read -r target; do
      [[ -n "$target" ]] && acceptance_targets+=("$target")
    done <<< "$targets_output"
  fi
  if [[ "${#acceptance_targets[@]}" -eq 0 ]]; then
    echo "selected coverage modules have no acceptance targets" >&2
    return 2
  fi
}

run_real_singlebox() {
  rm -rf "$coverage_root/raw" "$report_dir"
  rm -f \
    "$bcs_coverage_dir/cobertura.xml" \
    "$bcs_coverage_dir/coverage.txt" \
    "$bcs_coverage_dir/summary.json" \
    "$bcs_coverage_dir/endpoint_coverage.xml" \
    "$bcs_coverage_dir/endpoint_coverage.txt" \
    "$bcs_coverage_dir/cli_command_coverage.txt" \
    "$bcs_coverage_dir/cli_commands.log"
  mkdir -p "$coverage_root/raw" "$report_dir"
  phase_timing_file="$report_dir/phase-timings.tsv"
  printf 'phase\tstart_epoch\tend_epoch\tduration_seconds\tstatus\n' > "$phase_timing_file"
  find "$bcs_coverage_dir" -name '*.profraw' -delete 2>/dev/null || true
  find "$repo_root/apps/bcs/target/tmp" -name '*.profraw' -delete 2>/dev/null || true
  coverage_standalone_root="$coverage_root/standalone-openclaw"
  coverage_standalone_runtime="$coverage_root/standalone-runtime"
  repair_provider_fixture="$coverage_root/collaboration-repair-provider.json"
  # A new live Provider is registered below; never reuse a previous credential.
  rm -f "$repair_provider_fixture"
  echo "singlebox coverage real mode"
  echo "coverage_root: $coverage_root"
  echo "standalone_openclaw_root: $coverage_standalone_root"
  echo "standalone_runtime_dir: $coverage_standalone_runtime"
  echo "coverage_modules: ${coverage_modules[*]}"
  echo "acceptance_targets: ${acceptance_targets[*]}"
  echo "model_config_mode: $model_config_mode"
  cleanup_real_singlebox() {
    stop_bcs_companions
    flush_coverage_processes || true
    env OCB_SKIP_GIT_HOOKS=1 SINGLEBOX_MODEL_CONFIG_MODE="$model_config_mode" \
      STANDALONE_OPENCLAW_ROOT="$coverage_standalone_root" \
      STANDALONE_RUNTIME_DIR="$coverage_standalone_runtime" \
      bash "$repo_root/singlebox/singlebox.sh" --standalone stop all || true
    bcs_e2e_mock_stop || true
    # This file contains the ephemeral Provider admin credential, not evidence.
    # Remove it on both normal completion and EXIT-trap failure cleanup.
    rm -f "$repair_provider_fixture"
  }
  trap cleanup_real_singlebox EXIT
  trap 'exit 143' TERM
  trap 'exit 130' INT
  bcs_e2e_mock_start "$coverage_root/mock-services"
  # BCS_SKIP_DEBUG_BUILD=1: `setup all` would otherwise compile the
  # non-instrumented bcs/bcs-cli/bcs-admin into src/bcs/target/debug, a full
  # cargo build (~2 minutes in CI) that nothing in this run uses. The
  # `--with-bcs-coverage start all` below builds the instrumented binaries
  # under src/bcs/target/cov-e2e and exports BCS_BIN/BCS_CLI_BIN; the BCS e2e
  # reads the same instrumented bcs-cli.
  timed_phase "stack_setup" env OCB_SKIP_GIT_HOOKS=1 BCS_SKIP_DEBUG_BUILD=1 SINGLEBOX_MODEL_CONFIG_MODE="$model_config_mode" \
    STANDALONE_OPENCLAW_ROOT="$coverage_standalone_root" \
    STANDALONE_RUNTIME_DIR="$coverage_standalone_runtime" \
    bash "$repo_root/singlebox/singlebox.sh" --standalone setup all
  launch_coverage_stack() {
    # Gateway, BCS, and Backend use different env var names for the same HMAC
    # principal contract. All three ends must receive the same coverage key.
    env SINGLEBOX_COVERAGE=1 SINGLEBOX_COVERAGE_DIR="$coverage_root/raw" OCB_SKIP_GIT_HOOKS=1 SINGLEBOX_MODEL_CONFIG_MODE="$model_config_mode" \
    principal_signing_key="$SINGLEBOX_GATEWAY_PRINCIPAL_SIGNING_KEY" \
    AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE="$SINGLEBOX_GATEWAY_PRINCIPAL_SIGNING_KEY" \
    AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE="$SINGLEBOX_GATEWAY_PRINCIPAL_SIGNING_KEY" \
    BCS_DEBUG=true BCS_COVERAGE_FORCE_REBUILD="${BCS_COVERAGE_FORCE_REBUILD:-1}" \
    BCS_BIN="$bcs_coverage_dir/llvm-cov-target/debug/bcs" \
    BCS_CLI_BIN="$bcs_coverage_dir/llvm-cov-target/debug/bcs-cli" \
    LLVM_PROFILE_FILE="$bcs_coverage_dir/llvm-cov-target/bcs-%m-%p.profraw" \
    BACKEND_READY_ATTEMPTS="${BACKEND_READY_ATTEMPTS:-120}" \
    STANDALONE_OPENCLAW_ROOT="$coverage_standalone_root" \
    STANDALONE_RUNTIME_DIR="$coverage_standalone_runtime" \
    SINGLEBOX_ACCEPTANCE_REPAIR_PROVIDER_FILE="$repair_provider_fixture" \
    SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF="${SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF:-1}" \
    SINGLEBOX_ACCEPTANCE_KNOWLEDGE_FIXTURE_FILE="$repo_root/apps/backend/tests/community/acceptance/bot_knowledge/fixture_catalog.json" \
    SINGLEBOX_ACCEPTANCE_MCP_FIXTURE_FILE="$repo_root/apps/backend/tests/community/acceptance/mcp/fixture_mcp_center.json" \
    bash "$repo_root/singlebox/singlebox.sh" --standalone --with-bcs-coverage "$@"
  }
  timed_phase "stack_start" launch_coverage_stack start all
  timed_phase "repair_provider_provision" env SINGLEBOX_COVERAGE=1 "${reporter_command[@]}" \
    "$script_dir/collaboration_repair_provider.py" provision \
    --bcs-url "http://127.0.0.1:${BCS_PORT:-21000}" \
    --mock-url "$BCS_E2E_MOCK_BASE_URL" --fixture "$repair_provider_fixture"
  # Attribute mutation is startup-allowlisted. Regenerate only the disposable
  # BCS config; Backend and bot gateways keep their original live processes.
  timed_phase "repair_bcs_restart" launch_coverage_stack restart bcs
  local acceptance_status=0
  local bcs_e2e_status=0
  local backend_coverage_status=0
  local baas_coverage_status=0
  local summary_status=0
  local module_status=0
  local artifact_status=0
  start_bcs_companions
  timed_phase "backend_acceptance" run_acceptance_smoke || acceptance_status=$?
  timed_phase "bcs_shared_stories" run_bcs_e2e --shared-tests-only || bcs_e2e_status=$?
  local companion_status=0 aggregate_status=0
  timed_phase "bcs_companion_join" wait_bcs_companions || companion_status=$?
  # Both producer branches have exited. Only now stop the shared service,
  # flush/merge profiles, and execute the unchanged coverage/endpoint gates.
  timed_phase "bcs_coverage_report" run_bcs_e2e --aggregate-only || aggregate_status=$?
  if [[ "$bcs_e2e_status" -eq 0 ]]; then
    bcs_e2e_status="$companion_status"
  fi
  if [[ "$bcs_e2e_status" -eq 0 ]]; then
    bcs_e2e_status="$aggregate_status"
  fi
  coverage_flush_and_cleanup() {
    flush_coverage_processes
    cleanup_real_singlebox
  }
  timed_phase "coverage_flush_and_cleanup" coverage_flush_and_cleanup || true
  trap - EXIT TERM INT
  # Backend and BaaS coverage are independent (separate project dirs, data
  # files, and report paths), so render them concurrently. Each writes to its
  # own log that is replayed afterwards so the output stays readable.
  local backend_report_log="$coverage_root/backend-report.log"
  local baas_report_log="$coverage_root/baas-report.log"
  local backend_report_pid baas_report_pid
  local report_phase_start report_phase_end
  report_phase_start="$(date +%s)"
  combine_python_coverage "backend" "$repo_root/apps/backend" "$coverage_root/raw/backend" \
    > "$backend_report_log" 2>&1 &
  backend_report_pid=$!
  combine_python_coverage "baas" "$repo_root/apps/baas" "$coverage_root/raw/baas" \
    > "$baas_report_log" 2>&1 &
  baas_report_pid=$!
  wait "$backend_report_pid" || backend_coverage_status=$?
  wait "$baas_report_pid" || baas_coverage_status=$?
  cat "$backend_report_log" "$baas_report_log"
  report_phase_end="$(date +%s)"
  record_phase_timing "python_coverage_reports" "$report_phase_start" "$report_phase_end" "$((backend_coverage_status || baas_coverage_status))"
  local artifact_phase_start artifact_phase_end
  artifact_phase_start="$(date +%s)"
  write_summary_artifacts "$acceptance_status" "$bcs_e2e_status" || summary_status=$?
  write_module_artifacts || module_status=$?
  verify_required_artifacts || artifact_status=$?
  artifact_phase_end="$(date +%s)"
  record_phase_timing "summary_and_artifacts" "$artifact_phase_start" "$artifact_phase_end" "$((summary_status || module_status || artifact_status))"

  if [[ "$acceptance_status" -ne 0 ]]; then
    echo "singlebox coverage collected, but Backend acceptance failed with exit code $acceptance_status" >&2
    return "$acceptance_status"
  fi
  if [[ "$bcs_e2e_status" -ne 0 ]]; then
    echo "singlebox coverage collected, but BCS e2e failed with exit code $bcs_e2e_status" >&2
    return "$bcs_e2e_status"
  fi
  if [[ "$backend_coverage_status" -ne 0 ]]; then
    return "$backend_coverage_status"
  fi
  if [[ "$baas_coverage_status" -ne 0 ]]; then
    return "$baas_coverage_status"
  fi
  if [[ "$summary_status" -ne 0 ]]; then
    return "$summary_status"
  fi
  if [[ "$module_status" -ne 0 ]]; then
    return "$module_status"
  fi
  return "$artifact_status"
}

flush_coverage_processes() {
  local pids pid
  pids="$(
    ps ax -o pid= -o command= 2>/dev/null | \
      awk -v root="$repo_root" 'index($0, root) && index($0, "coverage run") {print $1}'
  )"
  [ -n "$pids" ] || return 0
  for pid in $pids; do
    kill -USR1 "$pid" 2>/dev/null || true
  done
  sleep 2
}

run_acceptance_smoke() {
  local acceptance_log="$report_dir/acceptance.log"
  local junit_report="$report_dir/acceptance-junit.xml"
  local owner_target="tests/community/acceptance/bot_ownership_sync"
  local owner_selected=0
  local owner_workers="${SINGLEBOX_ACCEPTANCE_OWNER_WORKERS:-3}"
  local serial_status=0
  local owner_status=0
  local target
  local serial_targets=()
  local owner_junit="$report_dir/acceptance-owner-junit.xml"
  local serial_junit="$report_dir/acceptance-serial-junit.xml"
  local rc=0

  for target in "${acceptance_targets[@]}"; do
    if [ "$target" = "$owner_target" ]; then
      owner_selected=1
    else
      serial_targets+=("$target")
    fi
  done
  if [ "$owner_selected" -eq 1 ]; then
    case "$owner_workers" in
      1|2|3) ;;
      *) echo "SINGLEBOX_ACCEPTANCE_OWNER_WORKERS must be 1, 2 or 3" >&2; return 2 ;;
    esac
  fi

  (
    cd "$repo_root/apps/backend"
    if [ "$owner_selected" -eq 0 ]; then
      RUN_ACCEPTANCE=1 \
        SINGLEBOX_ACCEPTANCE_REUSE_LIVE=1 \
        SINGLEBOX_ACCEPTANCE_KEEP_ARTIFACTS=1 \
        SINGLEBOX_ACCEPTANCE_REPAIR_PROVIDER_FILE="$repair_provider_fixture" \
        SINGLEBOX_ACCEPTANCE_BCS_ADMIN_BIN="$bcs_coverage_dir/llvm-cov-target/debug/bcs-admin" \
        SINGLEBOX_ACCEPTANCE_BCS_CONFIG_DIR="$coverage_standalone_runtime/bcs-config" \
        uv run pytest "${acceptance_targets[@]}" -q --junitxml "$junit_report" || rc=$?
    else
      if [ "${#serial_targets[@]}" -gt 0 ]; then
        echo "Running non-owner acceptance targets serially"
        RUN_ACCEPTANCE=1 \
          SINGLEBOX_ACCEPTANCE_REUSE_LIVE=1 \
          SINGLEBOX_ACCEPTANCE_KEEP_ARTIFACTS=1 \
          SINGLEBOX_ACCEPTANCE_REPAIR_PROVIDER_FILE="$repair_provider_fixture" \
          SINGLEBOX_ACCEPTANCE_BCS_ADMIN_BIN="$bcs_coverage_dir/llvm-cov-target/debug/bcs-admin" \
          SINGLEBOX_ACCEPTANCE_BCS_CONFIG_DIR="$coverage_standalone_runtime/bcs-config" \
          uv run pytest "${serial_targets[@]}" -q --junitxml "$serial_junit" || serial_status=$?
      fi

      echo "Running owner handoff acceptance with ${owner_workers} worker(s)"
      RUN_ACCEPTANCE=1 \
        SINGLEBOX_ACCEPTANCE_REUSE_LIVE=1 \
        SINGLEBOX_ACCEPTANCE_KEEP_ARTIFACTS=1 \
        SINGLEBOX_ACCEPTANCE_REPAIR_PROVIDER_FILE="$repair_provider_fixture" \
        SINGLEBOX_ACCEPTANCE_BCS_ADMIN_BIN="$bcs_coverage_dir/llvm-cov-target/debug/bcs-admin" \
        SINGLEBOX_ACCEPTANCE_BCS_CONFIG_DIR="$coverage_standalone_runtime/bcs-config" \
        uv run pytest "$owner_target" -q -n "$owner_workers" --dist=load --junitxml "$owner_junit" || owner_status=$?

      if [ "${#serial_targets[@]}" -gt 0 ] && [ -s "$serial_junit" ] && [ -s "$owner_junit" ]; then
        "${reporter_command[@]}" "$script_dir/merge_junit_reports.py" \
          "$junit_report" "$serial_junit" "$owner_junit" || rc=$?
      elif [ -s "$owner_junit" ]; then
        cp "$owner_junit" "$junit_report" || rc=$?
      elif [ -s "$serial_junit" ]; then
        cp "$serial_junit" "$junit_report" || rc=$?
      fi
      if [ "$serial_status" -ne 0 ] || [ "$owner_status" -ne 0 ]; then
        rc=1
      fi
    fi
    exit "$rc"
  ) > "$acceptance_log" 2>&1 || rc=$?
  cat "$acceptance_log"
  [ "$rc" -eq 0 ] || return "$rc"
  [ -s "$junit_report" ] || {
    echo "missing acceptance junit artifact: $junit_report" >&2
    return 1
  }
}

run_bcs_e2e() {
  local bcs_report_dir="$report_dir/bcs"
  local bcs_log="$bcs_report_dir/e2e.log"
  local phase="full"
  case "${1:-}" in
    --shared-tests-only) phase="shared-stories" ;;
    --aggregate-only) phase="aggregate" ;;
  esac
  local phase_log="$bcs_report_dir/$phase.log"
  local rc=0
  local artifact
  mkdir -p "$bcs_report_dir"
  printf '%s\n' \
    "BCS e2e: --skip-start --bcs-line-min $bcs_line_min --bcs-method-min $bcs_method_min" \
    > "$phase_log"
  env \
    STANDALONE_OPENCLAW_ROOT="$coverage_standalone_root" \
    STANDALONE_RUNTIME_DIR="$coverage_standalone_runtime" \
    BCS_BOTS_DATA_DIR="$coverage_standalone_root/profiles" \
    BCS_E2E_CHANGED_LINE_MIN="${BCS_E2E_CHANGED_LINE_MIN:-65}" \
    BCS_E2E_CHANGED_BASE_REF="${BCS_E2E_CHANGED_BASE_REF:-${SINGLEBOX_BASE_REF:-origin/dev}}" \
    AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE="$SINGLEBOX_GATEWAY_PRINCIPAL_SIGNING_KEY" \
    bash "$repo_root/apps/bcs/scripts/e2e_coverage.sh" \
      --skip-start "$@" \
      --bcs-line-min "$bcs_line_min" \
      --bcs-method-min "$bcs_method_min" \
      >> "$phase_log" 2>&1 || rc=$?
  cat "$phase_log" >> "$bcs_log"
  cat "$phase_log"
  [[ "$phase" != shared-stories ]] || return "$rc"

  for artifact in \
    cobertura.xml \
    coverage.txt \
    summary.json \
    endpoint_coverage.xml \
    endpoint_coverage.txt \
    cli_command_coverage.txt \
    changed_line_coverage.json \
    oauth_e2e.json; do
    if [[ -f "$bcs_coverage_dir/$artifact" ]]; then
      cp "$bcs_coverage_dir/$artifact" "$bcs_report_dir/$artifact"
    fi
  done
  return "$rc"
}

combine_python_coverage() {
  local component="$1"
  local component_dir="$2"
  local raw_dir="$3"
  local combined_file="$report_dir/${component}.coverage"
  local json_report="$report_dir/${component}-coverage.json"
  local text_report="$report_dir/${component}-coverage.txt"
  local html_dir="$report_dir/html/${component}"
  local coverage_files=()
  local coverage_file

  while IFS= read -r coverage_file; do
    coverage_files+=("$coverage_file")
  done < <(find "$raw_dir" -type f -name '.coverage*' 2>/dev/null | sort)

  if [ "${#coverage_files[@]}" -eq 0 ]; then
    echo "missing ${component} coverage data under ${raw_dir}" >&2
    return 1
  fi

  (
    cd "$component_dir"
    COVERAGE_FILE="$combined_file" uv run coverage combine "${coverage_files[@]}"
    # The JSON, HTML, and text reports only read the combined data file, so
    # render them concurrently; the HTML report alone takes ~30s for backend.
    local json_pid html_pid text_pid
    local json_status=0 html_status=0 text_status=0
    COVERAGE_FILE="$combined_file" uv run coverage json -i -o "$json_report" &
    json_pid=$!
    COVERAGE_FILE="$combined_file" uv run coverage html -i -d "$html_dir" &
    html_pid=$!
    COVERAGE_FILE="$combined_file" uv run coverage report -i > "$text_report" &
    text_pid=$!
    wait "$json_pid" || json_status=$?
    wait "$html_pid" || html_status=$?
    wait "$text_pid" || text_status=$?
    if [ "$json_status" -ne 0 ]; then
      echo "${component} coverage json report failed with exit code $json_status" >&2
      exit "$json_status"
    fi
    if [ "$html_status" -ne 0 ]; then
      echo "${component} coverage html report failed with exit code $html_status" >&2
      exit "$html_status"
    fi
    if [ "$text_status" -ne 0 ]; then
      echo "${component} coverage text report failed with exit code $text_status" >&2
      exit "$text_status"
    fi
  )
}

jsonl_count() {
  local file="$1"
  if [ -f "$file" ]; then
    wc -l < "$file" | tr -d ' '
  else
    printf '0'
  fi
}

write_summary_artifacts() {
  local acceptance_status="$1"
  local bcs_e2e_status="$2"
  local backend_router_hits baas_router_hits
  backend_router_hits="$(jsonl_count "$coverage_root/raw/backend/router_hits.jsonl")"
  baas_router_hits="$(jsonl_count "$coverage_root/raw/baas/router_hits.jsonl")"

  "${reporter_command[@]}" - \
    "$report_dir" \
    "$model_config_mode" \
    "$backend_router_hits" \
    "$baas_router_hits" \
    "$acceptance_status" \
    "$bcs_e2e_status" \
    "${acceptance_targets[@]}" <<'PY'
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

report_dir = Path(sys.argv[1])
model_config_mode = sys.argv[2]
backend_router_hits = int(sys.argv[3])
baas_router_hits = int(sys.argv[4])
acceptance_status = int(sys.argv[5])
bcs_e2e_status = int(sys.argv[6])
acceptance_targets = sys.argv[7:]


def metric(covered, total):
    return {
        "covered": int(covered),
        "total": int(total),
        "percent": round((covered * 100.0 / total) if total else 0.0, 2),
    }


bcs_report_dir = report_dir / "bcs"
bcs_artifact_errors = []
line = {}
method = {}
endpoint_covered = 0
endpoint_total = 0
cli_covered = 0
cli_total = 0

try:
    bcs_summary = json.loads((bcs_report_dir / "summary.json").read_text(encoding="utf-8"))
    bcs_totals = bcs_summary["data"][0]["totals"]
    if not isinstance(bcs_totals, dict):
        raise TypeError("totals must be an object")
    line = bcs_totals.get("lines") or {}
    method = bcs_totals.get("functions") or {}
    if not isinstance(line, dict) or not isinstance(method, dict):
        raise TypeError("lines and functions must be objects")
except (FileNotFoundError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
    line = {}
    method = {}
    bcs_artifact_errors.append(f"summary: {exc}")

try:
    endpoint_root = ET.parse(bcs_report_dir / "endpoint_coverage.xml").getroot()
    endpoint = endpoint_root.find("overall")
    if endpoint is None:
        raise ValueError("missing overall element")
    endpoint_covered = int(endpoint.attrib.get("covered", "0"))
    endpoint_total = int(endpoint.attrib.get("total", "0"))
except (FileNotFoundError, ET.ParseError, ValueError) as exc:
    bcs_artifact_errors.append(f"endpoint: {exc}")

try:
    cli_text = (bcs_report_dir / "cli_command_coverage.txt").read_text(encoding="utf-8")
    cli_match = re.search(r"coverage:\s*(\d+)\s*/\s*(\d+)", cli_text)
    if cli_match is None:
        raise ValueError("missing covered/total summary")
    cli_covered = int(cli_match.group(1))
    cli_total = int(cli_match.group(2))
except (FileNotFoundError, ValueError) as exc:
    bcs_artifact_errors.append(f"cli: {exc}")

bcs_system = {
    "name": "bcs",
    "e2e_status": "passed" if bcs_e2e_status == 0 else "failed",
    "runtime_line": metric(line.get("covered", 0), line.get("count", 0)),
    "method": metric(method.get("covered", 0), method.get("count", 0)),
    "router_api": metric(endpoint_covered, endpoint_total),
    "cli_command": metric(cli_covered, cli_total),
    "artifact_errors": bcs_artifact_errors,
    "artifacts": {
        "e2e_log": "bcs/e2e.log",
        "oauth_e2e": "bcs/oauth_e2e.json",
        "cobertura": "bcs/cobertura.xml",
        "coverage_text": "bcs/coverage.txt",
        "summary": "bcs/summary.json",
        "endpoint_xml": "bcs/endpoint_coverage.xml",
        "endpoint_text": "bcs/endpoint_coverage.txt",
        "cli_text": "bcs/cli_command_coverage.txt",
    },
}

summary = {
    "mode": "real",
    "status": "passed" if acceptance_status == 0 and bcs_e2e_status == 0 else "failed",
    "stack": "standalone start all",
    "model_config_mode": model_config_mode,
    "acceptance": {
        "status": "passed" if acceptance_status == 0 else "failed",
        "targets": acceptance_targets,
        "junit": "acceptance-junit.xml",
    },
    "systems": {"bcs": bcs_system},
    "coverage": {
        "backend": {
            "router_hits": backend_router_hits,
            "plugin_evidence": "backend-coverage.json",
            "json": "backend-coverage.json",
            "html": "html/backend/index.html",
        },
        "baas": {
            "router_hits": baas_router_hits,
            "json": "baas-coverage.json",
            "html": "html/baas/index.html",
        },
    },
}

report_dir.mkdir(parents=True, exist_ok=True)
(report_dir / "summary.json").write_text(
    json.dumps(summary, indent=2) + "\n",
    encoding="utf-8",
)
(report_dir / "summary.md").write_text(
    "\n".join(
        [
            "# Singlebox Coverage Summary",
            "",
            "- mode: real",
            "- stack: standalone start all",
            f"- model config: {model_config_mode}",
            f"- acceptance: {', '.join(acceptance_targets)}",
            f"- backend router hits: {backend_router_hits}",
            "- backend plugin evidence: backend-coverage.json",
            f"- baas router hits: {baas_router_hits}",
            f"- bcs e2e: {bcs_system['e2e_status']}",
            f"- bcs runtime line: {bcs_system['runtime_line']['percent']:.2f}%",
            f"- bcs method: {bcs_system['method']['percent']:.2f}%",
            f"- bcs router API: {bcs_system['router_api']['percent']:.2f}%",
            f"- bcs CLI commands: {bcs_system['cli_command']['percent']:.2f}%",
            "",
        ]
    ),
    encoding="utf-8",
)
(report_dir / "dashboard.html").write_text(
    "<!doctype html><meta charset='utf-8'><title>Singlebox Coverage</title>"
    "<h1>Singlebox Coverage</h1>"
    f"<p>Acceptance: {', '.join(acceptance_targets)}</p>"
    f"<p>Backend router hits: {backend_router_hits}</p>"
    "<p>Backend plugin evidence: backend-coverage.json</p>"
    f"<p>BaaS router hits: {baas_router_hits}</p>"
    f"<p>BCS runtime line: {bcs_system['runtime_line']['percent']:.2f}%</p>"
    f"<p>BCS method: {bcs_system['method']['percent']:.2f}%</p>"
    f"<p>BCS Router API: {bcs_system['router_api']['percent']:.2f}%</p>"
    f"<p>BCS CLI commands: {bcs_system['cli_command']['percent']:.2f}%</p>",
    encoding="utf-8",
)
PY
}

write_module_artifacts() {
  local module_args=()
  local module_name
  for module_name in "${coverage_modules[@]}"; do
    module_args+=(--module "$module_name")
  done
  "${reporter_command[@]}" "$script_dir/singlebox_coverage_report.py" \
    --manifest "$module_manifest" \
    "${module_args[@]}" \
    --coverage-json "$report_dir/backend-coverage.json" \
    --router-hits "$coverage_root/raw/backend/router_hits.jsonl" \
    --report-dir "$report_dir"
}

verify_required_artifacts() {
  local required=(
    "$report_dir/acceptance-junit.xml"
    "$report_dir/acceptance.log"
    "$report_dir/backend-coverage.json"
    "$report_dir/backend-coverage.txt"
    "$report_dir/html/backend/index.html"
    "$report_dir/baas-coverage.json"
    "$report_dir/baas-coverage.txt"
    "$report_dir/html/baas/index.html"
    "$report_dir/bcs/companion-timings.json"
    "$report_dir/bcs/e2e.log"
    "$report_dir/bcs/oauth_e2e.json"
    "$report_dir/bcs/cobertura.xml"
    "$report_dir/bcs/coverage.txt"
    "$report_dir/bcs/summary.json"
    "$report_dir/bcs/endpoint_coverage.xml"
    "$report_dir/bcs/endpoint_coverage.txt"
    "$report_dir/bcs/cli_command_coverage.txt"
    "$report_dir/summary.json"
    "$report_dir/summary.md"
    "$report_dir/dashboard.html"
    "$report_dir/phase-timings.tsv"
  )
  local file
  for file in "${required[@]}"; do
    if [ ! -s "$file" ]; then
      echo "missing required singlebox coverage artifact: $file" >&2
      return 1
    fi
  done
}

case "$mode" in
  real)
    resolve_reporter_command
    "${reporter_command[@]}" "$script_dir/singlebox_coverage_manifest_check.py" \
      --manifest "$module_manifest" \
      --backend-root "$repo_root/apps/backend"
    resolve_coverage_scope
    run_real_singlebox
    ;;
  *)
    echo "unknown singlebox coverage mode: $mode" >&2
    exit 2
    ;;
esac

echo "singlebox coverage gate passed"
