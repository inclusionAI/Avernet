#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"

export PATH="${HOME}/.cargo/bin:${PATH}"
# BCSFuse currently pins faiss-cpu 1.8.0, whose published wheels stop at
# CPython 3.12. Keep local pre-push aligned with the GitHub workflow instead of
# accidentally selecting a newer host Python during `uv sync`.
export UV_PYTHON="${UV_PYTHON:-3.12}"

coverage_root="${SINGLEBOX_COVERAGE_ROOT:-$repo_root/scripts/.dependencies/coverage/singlebox}"
report_dir="$coverage_root/reports"
mode="${SINGLEBOX_COVERAGE_MODE:-real}"
model_config_mode="${SINGLEBOX_COVERAGE_MODEL_CONFIG_MODE:-mock}"
module_manifest="$script_dir/singlebox_coverage_modules.yaml"
bcs_coverage_dir="$repo_root/src/bcs/target/cov-e2e"
bcs_line_min="${SINGLEBOX_COVERAGE_BCS_LINE_MIN:-40}"
bcs_method_min="${SINGLEBOX_COVERAGE_BCS_METHOD_MIN:-36}"
coverage_standalone_root=""
coverage_standalone_runtime=""
requested_modules=()
acceptance_targets=()
explicit_acceptance_targets=()
coverage_modules=()
reporter_command=()
source "$repo_root/src/bcs/scripts/e2e-test/mock_services.sh"

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
  rm -f \
    "$bcs_coverage_dir/cobertura.xml" \
    "$bcs_coverage_dir/coverage.txt" \
    "$bcs_coverage_dir/summary.json" \
    "$bcs_coverage_dir/endpoint_coverage.xml" \
    "$bcs_coverage_dir/endpoint_coverage.txt" \
    "$bcs_coverage_dir/cli_command_coverage.txt" \
    "$bcs_coverage_dir/cli_commands.log"
  mkdir -p "$coverage_root/raw" "$report_dir"
  coverage_standalone_root="$coverage_root/standalone-openclaw"
  coverage_standalone_runtime="$coverage_root/standalone-runtime"
  echo "singlebox coverage real mode"
  echo "coverage_root: $coverage_root"
  echo "standalone_openclaw_root: $coverage_standalone_root"
  echo "standalone_runtime_dir: $coverage_standalone_runtime"
  echo "coverage_modules: ${coverage_modules[*]}"
  echo "acceptance_targets: ${acceptance_targets[*]}"
  echo "model_config_mode: $model_config_mode"
  cleanup_real_singlebox() {
    flush_coverage_processes || true
    env OCB_SKIP_GIT_HOOKS=1 SINGLEBOX_MODEL_CONFIG_MODE="$model_config_mode" \
      STANDALONE_OPENCLAW_ROOT="$coverage_standalone_root" \
      STANDALONE_RUNTIME_DIR="$coverage_standalone_runtime" \
      bash "$repo_root/scripts/singlebox.sh" --standalone stop all || true
    bcs_e2e_mock_stop || true
  }
  trap cleanup_real_singlebox EXIT
  bcs_e2e_mock_start "$coverage_root/mock-services"
  env OCB_SKIP_GIT_HOOKS=1 SINGLEBOX_MODEL_CONFIG_MODE="$model_config_mode" \
    STANDALONE_OPENCLAW_ROOT="$coverage_standalone_root" \
    STANDALONE_RUNTIME_DIR="$coverage_standalone_runtime" \
    bash "$repo_root/scripts/singlebox.sh" --standalone setup all
  env SINGLEBOX_COVERAGE=1 SINGLEBOX_COVERAGE_DIR="$coverage_root/raw" OCB_SKIP_GIT_HOOKS=1 SINGLEBOX_MODEL_CONFIG_MODE="$model_config_mode" \
    BCS_DEBUG=true BCS_COVERAGE_FORCE_REBUILD="${BCS_COVERAGE_FORCE_REBUILD:-1}" \
    BACKEND_READY_ATTEMPTS="${BACKEND_READY_ATTEMPTS:-120}" \
    STANDALONE_OPENCLAW_ROOT="$coverage_standalone_root" \
    STANDALONE_RUNTIME_DIR="$coverage_standalone_runtime" \
    SINGLEBOX_ACCEPTANCE_MCP_FIXTURE_FILE="$repo_root/src/backend/tests/community/acceptance/mcp/fixture_mcp_center.json" \
    bash "$repo_root/scripts/singlebox.sh" --standalone --with-bcs-coverage start all
  local acceptance_status=0
  local bcs_e2e_status=0
  local backend_coverage_status=0
  local baas_coverage_status=0
  local summary_status=0
  local module_status=0
  local artifact_status=0
  run_acceptance_smoke || acceptance_status=$?
  run_bcs_e2e || bcs_e2e_status=$?
  flush_coverage_processes
  cleanup_real_singlebox
  trap - EXIT
  combine_python_coverage "backend" "$repo_root/src/backend" "$coverage_root/raw/backend" || backend_coverage_status=$?
  combine_python_coverage "baas" "$repo_root/src/baas" "$coverage_root/raw/baas" || baas_coverage_status=$?
  write_summary_artifacts "$acceptance_status" "$bcs_e2e_status" || summary_status=$?
  write_module_artifacts || module_status=$?
  verify_required_artifacts || artifact_status=$?

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

run_bcs_e2e() {
  local bcs_report_dir="$report_dir/bcs"
  local bcs_log="$bcs_report_dir/e2e.log"
  local rc=0
  local artifact
  mkdir -p "$bcs_report_dir"
  printf '%s\n' \
    "BCS e2e: --skip-start --bcs-line-min $bcs_line_min --bcs-method-min $bcs_method_min" \
    > "$bcs_log"
  env \
    STANDALONE_OPENCLAW_ROOT="$coverage_standalone_root" \
    STANDALONE_RUNTIME_DIR="$coverage_standalone_runtime" \
    BCS_BOTS_DATA_DIR="$coverage_standalone_root/profiles" \
    bash "$repo_root/src/bcs/scripts/e2e_coverage.sh" \
      --skip-start \
      --bcs-line-min "$bcs_line_min" \
      --bcs-method-min "$bcs_method_min" \
      >> "$bcs_log" 2>&1 || rc=$?
  cat "$bcs_log"

  for artifact in \
    cobertura.xml \
    coverage.txt \
    summary.json \
    endpoint_coverage.xml \
    endpoint_coverage.txt \
    cli_command_coverage.txt; do
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
    "$report_dir/bcs/e2e.log"
    "$report_dir/bcs/cobertura.xml"
    "$report_dir/bcs/coverage.txt"
    "$report_dir/bcs/summary.json"
    "$report_dir/bcs/endpoint_coverage.xml"
    "$report_dir/bcs/endpoint_coverage.txt"
    "$report_dir/bcs/cli_command_coverage.txt"
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
    "${reporter_command[@]}" "$script_dir/singlebox_coverage_manifest_check.py" \
      --manifest "$module_manifest" \
      --backend-root "$repo_root/src/backend"
    resolve_coverage_scope
    run_real_singlebox
    ;;
  *)
    echo "unknown singlebox coverage mode: $mode" >&2
    exit 2
    ;;
esac

echo "singlebox coverage gate passed"
