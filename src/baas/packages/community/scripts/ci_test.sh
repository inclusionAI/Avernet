#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/lib/pipeline.sh"

# Consume --base / --head so they don't pollute $1 / $2 (the hook passes them)
mode="bare"
overlay="e2e-sqlite"
group=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --base)  shift 2 ;;
    --head)  shift 2 ;;
    --group) group="$2"; shift 2 ;;
    *)       break    ;;
  esac
done
[[ "${1:-}" ]] && mode="$1"
[[ "${2:-}" ]] && overlay="$2"

cd "$BAAS_DIR" || exit 1

case "$group" in
  ci)            run_ci_tests ;;
  e2e)           run_e2e_tests "$mode" "$overlay" ;;
  arch)          run_arch_tests ;;
  unit)          run_unit_tests ;;
  integration)   run_integration_tests ;;
  "")            run_ci_pipeline "$mode" "$overlay" ;;
  *)             echo "Unknown group: $group"; exit 1 ;;
esac
