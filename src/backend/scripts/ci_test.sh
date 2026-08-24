#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
backend_dir="$(cd "$script_dir/.." && pwd)"
repo_root="$(cd "$backend_dir/../.." && pwd)"
ci_workspace="${CITEST_WORKSPACE:-$repo_root}"
report_dir="$backend_dir/pytest_report"
junit_report="$report_dir/TEST-junit.xml"
coverage_report="$report_dir/TEST-cov.xml"
line_coverage_min="${BACKEND_CI_LINE_COVERAGE_MIN:-75}"
python_bin="$(command -v python || command -v python3 || true)"
base=""
head="HEAD"

run_without_git_local_env() {
  env \
    -u GIT_ALTERNATE_OBJECT_DIRECTORIES \
    -u GIT_CONFIG \
    -u GIT_CONFIG_PARAMETERS \
    -u GIT_CONFIG_COUNT \
    -u GIT_OBJECT_DIRECTORY \
    -u GIT_DIR \
    -u GIT_WORK_TREE \
    -u GIT_IMPLICIT_WORK_TREE \
    -u GIT_GRAFT_FILE \
    -u GIT_INDEX_FILE \
    -u GIT_NO_REPLACE_OBJECTS \
    -u GIT_REPLACE_REF_BASE \
    -u GIT_PREFIX \
    -u GIT_SHALLOW_FILE \
    -u GIT_COMMON_DIR \
    "$@"
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --base) base="$2"; shift 2 ;;
    --head) head="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# When --base is omitted (e.g. a direct local invocation), derive the
# changed-line coverage base ref so the gate runs locally with the same
# threshold as GitHub CI instead of being skipped. CI always passes an
# explicit --base, so this branch only affects local runs.
if [[ -z "$base" ]]; then
  # shellcheck source=../../../scripts/lib/resolve_base_ref.sh
  source "$repo_root/scripts/lib/resolve_base_ref.sh"
  base="$(resolve_base_ref)" || {
    echo "backend CI failed: could not resolve changed-line coverage base ref" >&2
    exit 1
  }
fi

cd "$backend_dir"
mkdir -p "$report_dir"

if [[ -z "$python_bin" ]]; then
  echo "backend CI failed: neither python nor python3 found" >&2
  exit 127
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "backend CI failed: uv not found" >&2
  exit 127
fi

if [[ "${BACKEND_CI_SKIP_INSTALL:-0}" != "1" ]]; then
  uv sync --frozen
fi
backend_python="$backend_dir/.venv/bin/python"
if [[ ! -x "$backend_python" ]]; then
  backend_python="$python_bin"
fi

# Parallel workers. ``auto`` = one worker per core; ``0`` runs pytest in-process
# with no xdist at all (needed for --pdb, and the escape hatch if a worker-level
# problem ever has to be bisected). Workers are separate processes, so the
# in-memory SQLite engine, the DI singletons and the FastAPI ``app`` object are
# isolated per worker for free; ``--dist loadfile`` additionally keeps every test
# in a file on one worker so file-local ordering is preserved.
#
# ``auto`` is also the measured optimum, not just the obvious default: every
# worker collects the whole suite for itself (~80s of the run), so a worker
# past the core count adds more collection than it removes test time. On the
# 4-core runner this targets — full suite, sysmon coverage — auto/4 took 276s,
# -n 6 took 306s, and -n 8 took 367s. Raise it only along with the cores.
pytest_workers="${BACKEND_CI_PYTEST_WORKERS:-auto}"
xdist_args=()
if [[ "$pytest_workers" != "0" ]]; then
  xdist_args=(-n "$pytest_workers" --dist loadfile)
fi

# Coverage measurement core. ``sysmon`` is coverage.py's PEP 669 (sys.monitoring)
# backend, available on the 3.12 interpreter this project pins. It is
# substantially cheaper than the default settrace core — measured on the full
# suite under ``-n 4``: 418.56s default vs 214.60s sysmon, same 85% total. Only
# line coverage is collected here (no ``--cov-branch``), which is what sysmon
# supports well before 3.14. Set ``BACKEND_CI_COVERAGE_CORE=ctrace`` to fall back.
export COVERAGE_CORE="${BACKEND_CI_COVERAGE_CORE:-sysmon}"

# No ``-v``: at 14k tests it prints ~14k lines that GitHub then has to ingest,
# for ~30s of the run and no information the job does not already keep. A
# failure prints its full section at any verbosity, and the per-test record is
# in the JUnit XML this step writes and the job uploads.
#
# No ``term-missing``: it prints one line per source file under coverage
# (~1,340 lines and growing) purely for a human reading the raw log.
# report_check.py below is the only consumer that matters and it reads the
# XML report, never stdout, so the terminal table adds log volume with no
# gate depending on it.
set +e
DEPLOY_PROFILE=test \
PYTHONPATH="$backend_dir/src:$backend_dir:${PYTHONPATH:-}" \
run_without_git_local_env "$backend_python" -m pytest tests/community \
  "${xdist_args[@]}" \
  --continue-on-collection-errors \
  --junitxml="$junit_report" \
  --cov="$ci_workspace/src/backend/src" \
  --cov-report="xml:$coverage_report"
pytest_status=$?
set -e

touch "$junit_report" "$coverage_report"
if [[ "$pytest_status" -ne 0 ]]; then
  echo "backend CI failed: pytest did not pass cleanly" >&2
  exit "$pytest_status"
fi

check_args=(
  "$repo_root/scripts/ci/report_check.py"
  --junit "$junit_report"
  --coverage "$coverage_report"
  --source-root "$repo_root/src/backend/src"
  --min-case-pass-rate 100
  --min-line-coverage "$line_coverage_min"
)
if [[ -n "$base" ]]; then
  check_args+=(--base "$base" --head "$head" --min-change-line-coverage 80)
fi
"$python_bin" "${check_args[@]}"
echo "backend CI gate passed"
