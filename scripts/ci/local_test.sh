#!/usr/bin/env bash
# Local test orchestration for Avernet. Mirrors scripts/ci/pre_push.sh's module
# selection and dispatch logic, but is invoked by the root `justfile` for local
# developers. Each affected module's `scripts/ci_test.sh --base <base> --head HEAD`
# is invoked, which enforces the same changed-line coverage gate as GitHub CI
# (.github/workflows/unit-tests.yml).
#
# Baseline resolution: see scripts/ci/resolve_test_baseline.sh. The resolved
# baseline (merge-base with HEAD of the configured target branch) is the diff
# range used by every module's changed-line coverage gate.
#
# Modes:
#   default            run tests WITH the changed-line coverage gate (--base)
#   --no-cov           skip the changed-line coverage gate (fast iteration;
#                      does NOT satisfy the pre-push/PR coverage gate)
#   --base <ref>       pass an explicit baseline to resolve_test_baseline.sh
#   AVERNET_TEST_BASE_REF, avernet.test.mergeTarget honored via resolver
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" \
  || { echo "error: must run inside a git repository" >&2; exit 1; }
cd "$repo_root"

no_cov=0
explicit_base=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --no-cov)
      no_cov=1
      shift
      ;;
    --base)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --base requires an argument" >&2
        exit 2
      fi
      explicit_base="$2"
      shift 2
      ;;
    -h|--help)
      cat >&2 <<'USAGE'
usage: local_test.sh [--no-cov] [--base <commit-or-ref>]

Runs the affected Avernet module tests, mirroring the pre-push module dispatch
and GitHub CI changed-line coverage gate.

  --no-cov      skip the changed-line coverage gate (fast iteration only;
                not a substitute for the pre-push / PR coverage gate)
  --base <ref>  explicit baseline (commit-ish or <remote>/<branch>)
USAGE
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

head="HEAD"
if ! head_sha="$(git rev-parse --verify "${head}^{commit}" 2>/dev/null)"; then
  echo "error: cannot resolve HEAD commit" >&2
  exit 1
fi

echo "== Avernet local test =="
echo "head: $head_sha"

resolver="$repo_root/scripts/ci/resolve_test_baseline.sh"

if [[ "$no_cov" == "1" ]]; then
  echo "mode: no-cov (changed-line coverage gate DISABLED)"
  echo "note: --no-cov does NOT satisfy the pre-push/PR changed-line coverage gate."
  base=""
else
  if [[ ! -x "$resolver" ]]; then
    echo "error: missing baseline resolver: $resolver" >&2
    exit 1
  fi
  if [[ -n "$explicit_base" ]]; then
    if ! base="$("$resolver" --base "$explicit_base")"; then
      exit 1
    fi
  else
    if ! base="$("$resolver")"; then
      exit 1
    fi
  fi
  echo "base: $base"
  echo "mode: with changed-line coverage gate (matches GitHub CI)"
fi

# --- Compute changed files for module selection ----------------------------
if [[ -n "$base" ]]; then
  changed_files="$(git diff --name-only "$base" "$head")"
else
  # No coverage gate -> still compute a best-effort change set from origin/dev
  # so module dispatch matches what changed. Failures here fall through to
  # "no changes detected" and the script exits 0 with a clear banner.
  changed_files="$(git diff --name-only "origin/dev" "$head" 2>/dev/null || true)"
fi

matches_any() {
  local pattern="$1"
  printf '%s\n' "$changed_files" | grep -Eq "$pattern"
}

run_module() {
  # $@ -> module ci_test.sh invocation (already includes --base/--head as needed)
  echo ""
  echo "== module: $* =="
  "$@"
}

# --- Empty-change fast path ------------------------------------------------
if [[ -z "$changed_files" ]]; then
  if [[ -n "$base" ]]; then
    echo "no committed changes in ($base..$head); changed-line coverage gate skipped (matches pre-push)"
  else
    echo "no committed changes detected (no origin/dev baseline); nothing to gate"
  fi
  exit 0
fi

# --- Module dispatch (mirrors scripts/ci/pre_push.sh) ----------------------
if matches_any '^src/backend/'; then
  if [[ "$no_cov" == "1" ]]; then
    run_module "$repo_root/src/backend/scripts/ci_test.sh"
  else
    run_module "$repo_root/src/backend/scripts/ci_test.sh" --base "$base" --head "$head"
  fi
fi

if matches_any '^src/baas/'; then
  if [[ "${OCB_LOCAL_TEST_ENABLE_BAAS:-1}" == "1" ]]; then
    if [[ "$no_cov" == "1" ]]; then
      run_module "$repo_root/src/baas/scripts/ci_test.sh"
    else
      run_module "$repo_root/src/baas/scripts/ci_test.sh" --base "$base" --head "$head"
    fi
  else
    echo "BaaS changes detected; BaaS local gate skipped (OCB_LOCAL_TEST_ENABLE_BAAS=0)"
  fi
fi

if matches_any '^src/engine/'; then
  if [[ "$no_cov" == "1" ]]; then
    run_module "$repo_root/src/engine/scripts/ci_test.sh"
  else
    run_module "$repo_root/src/engine/scripts/ci_test.sh" --base "$base" --head "$head"
  fi
fi

if matches_any '^src/bcs/'; then
  if [[ "${OCB_LOCAL_TEST_ENABLE_BCS:-1}" == "1" ]]; then
    if [[ "$no_cov" == "1" ]]; then
      run_module "$repo_root/src/bcs/scripts/ci_test.sh" --fast-fail
    else
      # BCS uses cov_gate.py for the changed-line gate; its ci_test.sh accepts
      # --base/--head and forwards to cov_gate.py the same way pre_push.sh does.
      run_module "$repo_root/src/bcs/scripts/ci_test.sh" --base "$base" --head "$head" --fast-fail
    fi
  else
    echo "BCS/BCN changes detected; BCS/BCN local gate skipped (OCB_LOCAL_TEST_ENABLE_BCS=0)"
  fi
fi

if matches_any '^src/gateway/'; then
  if [[ "$no_cov" == "1" ]]; then
    run_module "$repo_root/src/gateway/scripts/ci_test.sh"
  else
    run_module "$repo_root/src/gateway/scripts/ci_test.sh" --base "$base" --head "$head"
  fi
fi

if matches_any '^src/frontend/'; then
  if [[ "$no_cov" == "1" ]]; then
    run_module "$repo_root/src/frontend/scripts/ci_test.sh"
  else
    run_module "$repo_root/src/frontend/scripts/ci_test.sh" --base "$base" --head "$head"
  fi
fi

echo ""
if [[ "$no_cov" == "1" ]]; then
  echo "Avernet local test (no-cov) done. Changed-line coverage gate was NOT enforced."
else
  echo "Avernet local test done. Changed-line coverage gate passed."
fi