#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
baas_root="$(cd "$script_dir/.." && pwd)"
repo_root="$(cd "$baas_root/../.." && pwd)"
baas_dir="${BAAS_COMMUNITY_DIR:-$baas_root}"
ci_workspace="${CITEST_WORKSPACE:-$repo_root}"
report_dir="$baas_dir/pytest_report"
unit_report="$report_dir/ci.xml"
coverage_report="$report_dir/cov-ci.xml"
line_coverage_min="${BAAS_CI_LINE_COVERAGE_MIN:-90}"
python_bin="$(command -v python || command -v python3 || true)"
base=""
head="HEAD"

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --base) base="$2"; shift 2 ;;
    --head) head="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ ! -d "$baas_dir" ]]; then
  echo "baas CI failed: community package not found: $baas_dir" >&2
  exit 1
fi
if [[ -z "$python_bin" ]]; then
  echo "baas CI failed: neither python nor python3 found" >&2
  exit 127
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "baas CI failed: uv not found" >&2
  exit 127
fi

cd "$baas_dir"
if [[ "${BAAS_CI_SKIP_INSTALL:-0}" != "1" ]]; then
  uv sync --frozen
fi
baas_python="$baas_dir/.venv/bin/python"
if [[ ! -x "$baas_python" ]]; then
  baas_python="$python_bin"
fi
mkdir -p "$report_dir"

set +e
source scripts/lib/pipeline.sh && run_ci_pipeline bare e2e-sqlite
baas_ci_status=$?
set -e

touch "$unit_report" "$coverage_report"
if [[ "$baas_ci_status" -ne 0 ]]; then
  echo "BaaS CI failed: baas ci did not pass cleanly" >&2
  exit "$baas_ci_status"
fi

echo "=== CI COVERAGE REPORT ==="
ci_check_args=(
  "$repo_root/scripts/ci/report_check.py"
  --junit "$unit_report"
  --coverage "$coverage_report"
  --source-root "$baas_dir/src"
  --min-case-pass-rate 100
  --min-line-coverage "$line_coverage_min"
)
if [[ -n "$base" ]]; then
  ci_check_args+=(--base "$base" --head "$head" --min-change-line-coverage 90)
fi
"$python_bin" "${ci_check_args[@]}"
echo "=== END CI COVERAGE REPORT ==="
echo "BAAS CI GATE PASSED"

echo "=== E2E COVERAGE REPORT ==="
asgi_check_args=(
  "$repo_root/scripts/ci/report_check.py"
  --junit "$report_dir/asgi.xml"
  --coverage "$report_dir/cov-asgi.xml"
  --source-root "$baas_dir/src"
  --min-case-pass-rate 100
  --min-line-coverage "40"
)
#if [[ -n "$base" ]]; then
#  asgi_check_args+=(--base "$base" --head "$head" --min-change-line-coverage 40)
#fi
"$python_bin" "${asgi_check_args[@]}"
echo "=== END ASGI COVERAGE REPORT ==="
echo "BAAS ASGI GATE PASSED"
