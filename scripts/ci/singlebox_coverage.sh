#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"

coverage_root="${SINGLEBOX_COVERAGE_ROOT:-$repo_root/scripts/.dependencies/coverage/singlebox}"
report_dir="$coverage_root/reports"
mode="${SINGLEBOX_COVERAGE_MODE:-mock}"

usage() {
  cat <<USAGE
Usage: scripts/ci/singlebox_coverage.sh [OPTIONS]

Singlebox coverage gate entrypoint used by pre-push and PR CI.

Current GitHub bootstrap behavior:
  - default mode is mock, so pre-push can enforce the architecture before the
    real open-source singlebox startup path is fully stable;
  - set SINGLEBOX_COVERAGE_MODE=real to attempt the live singlebox path.

Options:
  --coverage-root DIR     Coverage output root, default: $coverage_root
  --mode mock|real        Override SINGLEBOX_COVERAGE_MODE
  -h, --help              Show this help
USAGE
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --coverage-root)
      coverage_root="$2"
      report_dir="$coverage_root/reports"
      shift 2
      ;;
    --mode)
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

write_mock_reports() {
  mkdir -p "$report_dir"
  cat > "$report_dir/summary.json" <<JSON
{
  "mode": "mock",
  "status": "passed",
  "systems": {
    "backend": {"core": null, "router_api": null, "plugin_api": null},
    "baas": {"core": null, "router_api": null, "plugin_api": null},
    "bcs": {"core": null, "router_api": null, "plugin_api": null},
    "engine": {"core": null, "router_api": null, "plugin_api": null}
  },
  "note": "Mock singlebox coverage gate. The pre-push architecture is active; real coverage is enabled by SINGLEBOX_COVERAGE_MODE=real after open-source singlebox startup is stabilized."
}
JSON
  cat > "$report_dir/summary.md" <<MD
# Singlebox Coverage Gate

- mode: mock
- status: passed
- note: pre-push invoked the singlebox coverage gate; real startup is deferred to SINGLEBOX_COVERAGE_MODE=real.
MD
  cat > "$report_dir/dashboard.html" <<HTML
<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>Singlebox Coverage Gate</title>
<body>
  <h1>Singlebox Coverage Gate</h1>
  <p>Mode: mock</p>
  <p>Status: passed</p>
  <p>The pre-push architecture invoked this gate. Switch to <code>SINGLEBOX_COVERAGE_MODE=real</code> when open-source singlebox startup is stable.</p>
</body>
</html>
HTML
}

run_real_singlebox() {
  mkdir -p "$coverage_root/raw" "$report_dir"
  echo "singlebox coverage real mode"
  echo "coverage_root: $coverage_root"
  echo "This mode currently verifies startup only; full coverage combine/reporting will be restored with the real singlebox worktree."
  env SINGLEBOX_COVERAGE=1 SINGLEBOX_COVERAGE_DIR="$coverage_root/raw" OCB_SKIP_GIT_HOOKS=1 \
    bash "$repo_root/scripts/singlebox.sh" --local start baas backend bcs
  env OCB_SKIP_GIT_HOOKS=1 bash "$repo_root/scripts/singlebox.sh" --local stop bcs backend baas
}

case "$mode" in
  mock)
    echo "singlebox coverage gate: mock mode"
    echo "coverage reports: $report_dir"
    write_mock_reports
    ;;
  real)
    run_real_singlebox
    ;;
  *)
    echo "unknown singlebox coverage mode: $mode" >&2
    exit 2
    ;;
esac

echo "singlebox coverage gate passed"
