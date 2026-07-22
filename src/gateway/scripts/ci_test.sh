#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
gateway_root="$(cd "$script_dir/.." && pwd)"
repo_root="$(cd "$gateway_root/../.." && pwd)"
gateway_dir="${GATEWAY_COMMUNITY_DIR:-$gateway_root}"
report_dir="$gateway_dir/pytest_report"
unit_report="$report_dir/TEST-unit.xml"
coverage_report="$report_dir/TEST-cov.xml"
line_coverage_min="${GATEWAY_CI_LINE_COVERAGE_MIN:-80}"
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

if [[ -z "$base" ]]; then
  if git rev-parse --verify origin/dev >/dev/null 2>&1; then
    base="$(git merge-base "$head" origin/dev)"
    echo "auto-detected base: $base (merge-base of HEAD and origin/dev)"
  fi
fi

if [[ ! -d "$gateway_dir" ]]; then
  echo "gateway CI failed: community package not found: $gateway_dir" >&2
  exit 1
fi
if [[ -z "$python_bin" ]]; then
  echo "gateway CI failed: neither python nor python3 found" >&2
  exit 127
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "gateway CI failed: uv not found" >&2
  exit 127
fi

cd "$gateway_dir"
if [[ "${GATEWAY_CI_SKIP_INSTALL:-0}" != "1" ]]; then
  uv sync --frozen
fi
gateway_python="$gateway_dir/.venv/bin/python"
if [[ ! -x "$gateway_python" ]]; then
  gateway_python="$python_bin"
fi
mkdir -p "$report_dir"

set +e
source scripts/lib/pipeline.sh && run_ci_pipeline
gateway_ci_status=$?
set -e

touch "$unit_report" "$coverage_report"
if [[ "$gateway_ci_status" -ne 0 ]]; then
  echo "Gateway CI failed: did not pass cleanly" >&2
  exit "$gateway_ci_status"
fi

echo "--- unit coverage report ---"
check_args=(
  "$repo_root/scripts/ci/report_check.py"
  --junit "$unit_report"
  --coverage "$coverage_report"
  --source-root "$gateway_dir/src"
  --min-case-pass-rate 100
  --min-line-coverage "$line_coverage_min"
)
if [[ -n "$base" ]]; then
  check_args+=(--base "$base" --head "$head" --min-change-line-coverage 80)
fi
"$python_bin" "${check_args[@]}"
echo "--- end unit coverage report ---"
echo "gateway CI gate passed"
