#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"

export PATH="${HOME}/.cargo/bin:${PATH}"

coverage_root="${SINGLEBOX_COVERAGE_ROOT:-$repo_root/scripts/.dependencies/coverage/singlebox}"
report_dir="$coverage_root/reports"
mode="${SINGLEBOX_COVERAGE_MODE:-real}"
module_manifest="$script_dir/singlebox_coverage_modules.yaml"
coverage_standalone_root=""
coverage_standalone_runtime=""
requested_modules=()
acceptance_targets=()
explicit_acceptance_targets=()
coverage_modules=()
reporter_command=()

if [[ -n "${SINGLEBOX_COVERAGE_MODULE:-}" ]]; then
  requested_modules+=("$SINGLEBOX_COVERAGE_MODULE")
fi
if [[ -n "${SINGLEBOX_COVERAGE_ACCEPTANCE_TARGET:-}" ]]; then
  explicit_acceptance_targets+=("$SINGLEBOX_COVERAGE_ACCEPTANCE_TARGET")
fi

usage() {
  cat <<USAGE
Usage: scripts/ci/singlebox_coverage.sh [OPTIONS]

Singlebox coverage gate entrypoint used by pre-push and PR CI.

The default mode is real: pre-push starts the local singlebox coverage stack.

Options:
  --coverage-root DIR     Coverage output root, default: $coverage_root
  --mode real             Override SINGLEBOX_COVERAGE_MODE
  --acceptance-target PATH
                          Override manifest targets; may be repeated
  --module NAME           Run and gate one manifest module; may be repeated
                          Default: every enabled module in the manifest
  -h, --help              Show this help
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

if [[ ! -s "$module_manifest" ]]; then
  echo "missing singlebox coverage module manifest: $module_manifest" >&2
  exit 2
fi

resolve_reporter_command() {
  if [[ -n "${PYTHON:-}" ]]; then
    reporter_command=("$PYTHON")
  elif [[ -x "$repo_root/src/backend/.venv/bin/python" ]]; then
    reporter_command=("$repo_root/src/backend/.venv/bin/python")
  else
    reporter_command=(uv run --project "$repo_root/src/backend" python)
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
  mkdir -p "$coverage_root/raw" "$report_dir"
  coverage_standalone_root="$coverage_root/standalone-openclaw"
  coverage_standalone_runtime="$coverage_root/standalone-runtime"
  echo "singlebox coverage real mode"
  echo "coverage_root: $coverage_root"
  echo "standalone_openclaw_root: $coverage_standalone_root"
  echo "standalone_runtime_dir: $coverage_standalone_runtime"
  echo "coverage_modules: ${coverage_modules[*]}"
  echo "acceptance_targets: ${acceptance_targets[*]}"
  cleanup_real_singlebox() {
    flush_coverage_processes || true
    env OCB_SKIP_GIT_HOOKS=1 SINGLEBOX_MODEL_CONFIG_MODE=mock \
      STANDALONE_OPENCLAW_ROOT="$coverage_standalone_root" \
      STANDALONE_RUNTIME_DIR="$coverage_standalone_runtime" \
      bash "$repo_root/scripts/singlebox.sh" --standalone stop all || true
  }
  trap cleanup_real_singlebox EXIT
  env OCB_SKIP_GIT_HOOKS=1 SINGLEBOX_MODEL_CONFIG_MODE=mock \
    STANDALONE_OPENCLAW_ROOT="$coverage_standalone_root" \
    STANDALONE_RUNTIME_DIR="$coverage_standalone_runtime" \
    bash "$repo_root/scripts/singlebox.sh" --standalone setup all
  env SINGLEBOX_COVERAGE=1 SINGLEBOX_COVERAGE_DIR="$coverage_root/raw" OCB_SKIP_GIT_HOOKS=1 SINGLEBOX_MODEL_CONFIG_MODE=mock \
    BACKEND_READY_ATTEMPTS="${BACKEND_READY_ATTEMPTS:-120}" \
    STANDALONE_OPENCLAW_ROOT="$coverage_standalone_root" \
    STANDALONE_RUNTIME_DIR="$coverage_standalone_runtime" \
    bash "$repo_root/scripts/singlebox.sh" --standalone start all
  run_acceptance_smoke
  flush_coverage_processes
  cleanup_real_singlebox
  trap - EXIT
  combine_python_coverage "backend" "$repo_root/src/backend" "$coverage_root/raw/backend"
  combine_python_coverage "baas" "$repo_root/src/baas" "$coverage_root/raw/baas"
  write_summary_artifacts
  write_module_artifacts
  verify_required_artifacts
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
  local rc=0
  (
    cd "$repo_root/src/backend"
    RUN_ACCEPTANCE=1 \
      SINGLEBOX_ACCEPTANCE_REUSE_LIVE=1 \
      SINGLEBOX_ACCEPTANCE_KEEP_ARTIFACTS=1 \
      uv run pytest "${acceptance_targets[@]}" -q --junitxml "$junit_report"
  ) > "$acceptance_log" 2>&1 || rc=$?
  cat "$acceptance_log"
  [ "$rc" -eq 0 ] || return "$rc"
  [ -s "$junit_report" ] || {
    echo "missing acceptance junit artifact: $junit_report" >&2
    return 1
  }
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
    COVERAGE_FILE="$combined_file" uv run coverage json -i -o "$json_report"
    COVERAGE_FILE="$combined_file" uv run coverage html -i -d "$html_dir"
    COVERAGE_FILE="$combined_file" uv run coverage report -i > "$text_report"
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
  local backend_router_hits backend_plugin_hits baas_router_hits
  backend_router_hits="$(jsonl_count "$coverage_root/raw/backend/router_hits.jsonl")"
  backend_plugin_hits="$(jsonl_count "$coverage_root/raw/backend/plugin_hits.jsonl")"
  baas_router_hits="$(jsonl_count "$coverage_root/raw/baas/router_hits.jsonl")"

  "${reporter_command[@]}" - "$report_dir" "$backend_router_hits" "$backend_plugin_hits" "$baas_router_hits" "${acceptance_targets[@]}" <<'PY'
import json
import sys
from pathlib import Path

report_dir = Path(sys.argv[1])
backend_router_hits = int(sys.argv[2])
backend_plugin_hits = int(sys.argv[3])
baas_router_hits = int(sys.argv[4])
acceptance_targets = sys.argv[5:]

summary = {
    "mode": "real",
    "status": "passed",
    "stack": "standalone start all",
    "model_config_mode": "mock",
    "acceptance": {
        "targets": acceptance_targets,
        "junit": "acceptance-junit.xml",
    },
    "coverage": {
        "backend": {
            "router_hits": backend_router_hits,
            "plugin_hits": backend_plugin_hits,
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
            "- model config: mock",
            f"- acceptance: {', '.join(acceptance_targets)}",
            f"- backend router hits: {backend_router_hits}",
            f"- backend plugin hits: {backend_plugin_hits}",
            f"- baas router hits: {baas_router_hits}",
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
    f"<p>Backend plugin hits: {backend_plugin_hits}</p>"
    f"<p>BaaS router hits: {baas_router_hits}</p>",
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
    --plugin-hits "$coverage_root/raw/backend/plugin_hits.jsonl" \
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
    "$report_dir/summary.json"
    "$report_dir/summary.md"
    "$report_dir/dashboard.html"
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
    resolve_coverage_scope
    run_real_singlebox
    ;;
  *)
    echo "unknown singlebox coverage mode: $mode" >&2
    exit 2
    ;;
esac

echo "singlebox coverage gate passed"
