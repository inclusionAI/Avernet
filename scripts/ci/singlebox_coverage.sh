#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"

export PATH="${HOME}/.cargo/bin:${PATH}"

coverage_root="${SINGLEBOX_COVERAGE_ROOT:-$repo_root/scripts/.dependencies/coverage/singlebox}"
report_dir="$coverage_root/reports"
mode="${SINGLEBOX_COVERAGE_MODE:-real}"

usage() {
  cat <<USAGE
Usage: scripts/ci/singlebox_coverage.sh [OPTIONS]

Singlebox coverage gate entrypoint used by pre-push and PR CI.

The default mode is real: pre-push starts the local singlebox coverage stack.

Options:
  --coverage-root DIR     Coverage output root, default: $coverage_root
  --mode real             Override SINGLEBOX_COVERAGE_MODE
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

run_real_singlebox() {
  mkdir -p "$coverage_root/raw" "$report_dir"
  echo "singlebox coverage real mode"
  echo "coverage_root: $coverage_root"
  echo "This mode currently verifies startup only; full coverage combine/reporting will be restored with the real singlebox worktree."
  cleanup_real_singlebox() {
    env OCB_SKIP_GIT_HOOKS=1 SINGLEBOX_MODEL_CONFIG_MODE=mock \
      bash "$repo_root/scripts/singlebox.sh" --standalone stop bcs backend baas || true
  }
  trap cleanup_real_singlebox EXIT
  env SINGLEBOX_COVERAGE=1 SINGLEBOX_COVERAGE_DIR="$coverage_root/raw" OCB_SKIP_GIT_HOOKS=1 SINGLEBOX_MODEL_CONFIG_MODE=mock \
    bash "$repo_root/scripts/singlebox.sh" --standalone start baas backend bcs
  cleanup_real_singlebox
  trap - EXIT
}

case "$mode" in
  real)
    run_real_singlebox
    ;;
  *)
    echo "unknown singlebox coverage mode: $mode" >&2
    exit 2
    ;;
esac

echo "singlebox coverage gate passed"
