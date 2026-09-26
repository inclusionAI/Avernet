#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
proxy_root="$(cd "$script_dir/.." && pwd)"
repo_root="$(cd "$proxy_root/../.." && pwd)"
proxy_dir="${SANDBOXPROXY_COMMUNITY_DIR:-$proxy_root}"
report_dir="$proxy_dir/pytest_report"
unit_report="$report_dir/TEST-unit.xml"
coverage_report="$report_dir/TEST-cov.xml"
line_coverage_min="${SANDBOXPROXY_CI_LINE_COVERAGE_MIN:-90}"
python_bin="$(command -v python || command -v python3 || true)"
base=""
head="HEAD"
_run_mode="bare"
_run_overlay="e2e-sqlite"
_positional=()

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --base) base="$2"; shift 2 ;;
    --head) head="$2"; shift 2 ;;
    --group) shift 2 ;;
    --mode) _run_mode="$2"; shift 2 ;;
    --overlay) _run_overlay="$2"; shift 2 ;;
    *) _positional+=("$1"); shift ;;
  esac
done
# Trailing positionals are mode then overlay: "--group ci <mode> <overlay>".
[[ ${#_positional[@]} -ge 1 ]] && _run_mode="${_positional[0]}"
[[ ${#_positional[@]} -ge 2 ]] && _run_overlay="${_positional[1]}"

if [[ -z "$base" ]]; then
  source "$repo_root/scripts/lib/resolve_base_ref.sh"
  base="$(resolve_base_ref)" || {
    echo "sandbox-proxy CI failed: could not resolve changed-line coverage base ref" >&2
    exit 1
  }
fi

if [[ ! -d "$proxy_dir" ]]; then
  echo "sandbox-proxy CI failed: community package not found: $proxy_dir" >&2
  exit 1
fi
if [[ -z "$python_bin" ]]; then
  echo "sandbox-proxy CI failed: neither python nor python3 found" >&2
  exit 127
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "sandbox-proxy CI failed: uv not found" >&2
  exit 127
fi

cd "$proxy_dir"
if [[ "${SANDBOXPROXY_CI_SKIP_INSTALL:-0}" != "1" ]]; then
  uv sync --frozen
fi
proxy_python="$proxy_dir/.venv/bin/python"
if [[ ! -x "$proxy_python" ]]; then
  proxy_python="$python_bin"
fi
mkdir -p "$report_dir"

set +e
source scripts/lib/pipeline.sh && run_ci_pipeline "$_run_mode" "$_run_overlay"
proxy_ci_status=$?
set -e

touch "$unit_report" "$coverage_report"
if [[ "$proxy_ci_status" -ne 0 ]]; then
  echo "sandbox-proxy CI failed: did not pass cleanly" >&2
  exit "$proxy_ci_status"
fi

echo "--- unit coverage report ---"
check_args=(
  "$repo_root/scripts/ci/report_check.py"
  --junit "$unit_report"
  --coverage "$coverage_report"
  --source-root "$proxy_dir/src/sandboxproxy"
  --min-case-pass-rate 100
  --min-line-coverage "$line_coverage_min"
)
if [[ -n "$base" ]]; then
  check_args+=(--base "$base" --head "$head" --min-change-line-coverage 90)
fi
"$python_bin" "${check_args[@]}"
echo "Coverage report: file://$report_dir/html/index.html"
echo "--- end unit coverage report ---"
echo "sandbox-proxy CI gate passed"