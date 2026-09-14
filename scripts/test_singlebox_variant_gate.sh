#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SINGLEBOX="${ROOT}/scripts/singlebox.sh"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

# A broken FRONTEND_VARIANT config (typo'd value, or a teamclaw TEAMCLAW_DIR
# that was moved/deleted) must never brick the toolbox: read-only/lifecycle
# commands keep working — a live stack must always keep a script path to stop
# it — while setup/start still fail fast with the selection error before
# anything is built.
#
# .env.local values override environment variables (load_repo_env_file sources
# the file with `set -a`), so the broken-variant probes below are only
# meaningful when the checkout has no .env.local of its own. Hide it for the
# duration of the probes and restore it on exit; on CI the file does not exist.
if [ -f "${ROOT}/.env.local" ]; then
    mv "${ROOT}/.env.local" "${ROOT}/.env.local.hidden-by-variant-gate-test"
    restore_env_local() {
        [ -f "${ROOT}/.env.local.hidden-by-variant-gate-test" ] &&
            mv -f "${ROOT}/.env.local.hidden-by-variant-gate-test" "${ROOT}/.env.local" || true
    }
else
    restore_env_local() { :; }
fi
trap restore_env_local EXIT

status_probe() {
    FRONTEND_VARIANT="$1" TEAMCLAW_DIR="${2:-}" bash "${SINGLEBOX}" status all 2>&1
}
start_probe() {
    FRONTEND_VARIANT="$1" TEAMCLAW_DIR="${2:-}" bash "${SINGLEBOX}" start frontend 2>&1
}

# 1. A dangling TEAMCLAW_DIR must not block the read-only path: status still
#    runs to completion. The selection error itself is expected output (the
#    source-time select runs and fails before falling back); "degraded, not
#    bricked" means rc 0 plus a real status section under it.
out="$(status_probe teamclaw "${TEMP}/no-such-teamclaw-dir")" ||
    fail "status must stay usable when TEAMCLAW_DIR dangles; got: ${out}"
grep -q "Service Status" <<<"$out" ||
    fail "status produced no status section under a broken variant; got: ${out}"
grep -q "TEAMCLAW_DIR does not exist" <<<"$out" ||
    fail "the degraded status should still surface the selection error; got: ${out}"

# 2. An invalid variant string must not block it either.
out="$(status_probe invalid-variant "")" ||
    fail "status must stay usable under an invalid FRONTEND_VARIANT; got: ${out}"

# 3. The same broken configs must still fail START paths at the strict gate,
#    before any service work: nonzero rc, the selection error, and none of
#    the mode banner the dispatch only prints after the gate has passed.
out="$(start_probe teamclaw "${TEMP}/no-such-teamclaw-dir")" &&
    fail "start must fail fast when TEAMCLAW_DIR dangles"
grep -q "TEAMCLAW_DIR does not exist" <<<"$out" ||
    fail "start failure must name the selection error; got: ${out}"
if grep -q "STANDALONE MODE ENABLED" <<<"$out"; then
    fail "start did not fail fast (it reached the mode banner): ${out}"
fi

out="$(start_probe invalid-variant "")" &&
    fail "start must fail fast under an invalid FRONTEND_VARIANT"
grep -q "FRONTEND_VARIANT must be legacy, nextgen or teamclaw" <<<"$out" ||
    fail "start failure must name the invalid-variant error; got: ${out}"

# 4. bcs_frontend refuses non-legacy variants up front: the composite forces
#    the legacy panel scripts (devs:local:oss / devs:dev, which exist in no
#    other frontend) and composes no gateway for the non-legacy UI's
#    OpenAPI/auth routes. Loud refusal over a half-started stack. The guard
#    runs before any service call, so a function-level probe suffices.
export PROJECT_ROOT="$ROOT" LOG_DIR="$TEMP" DEP_DIR="$TEMP" FRONTEND_PORT=28800 BCS_PORT=28801
# shellcheck source=/dev/null
source "${ROOT}/scripts/utils.sh"
# shellcheck source=/dev/null
source "${ROOT}/scripts/modules/frontend.sh"
# shellcheck source=/dev/null
source "${ROOT}/scripts/modules/bcs_frontend.sh"
FRONTEND_VARIANT=nextgen frontend_select_variant
if out="$(FRONTEND_VARIANT=nextgen bcs_frontend_setup 2>&1)"; then
    fail "bcs_frontend_setup must refuse non-legacy variants"
fi
grep -q "serves the legacy BCS panel" <<<"$out" ||
    fail "bcs_frontend_setup refusal must explain the legacy constraint; got: ${out}"
if out="$(FRONTEND_VARIANT=teamclaw bcs_frontend_start 2>&1)"; then
    fail "bcs_frontend_start must refuse non-legacy variants"
fi
grep -q "serves the legacy BCS panel" <<<"$out" ||
    fail "bcs_frontend_start refusal must explain the legacy constraint; got: ${out}"

printf 'PASS: variant gate keeps lifecycle commands usable and starts fail-fast\n'
