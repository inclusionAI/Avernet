#!/usr/bin/env bash
# Shared base-ref resolver for local `just test` recipes.
#
# Local test recipes need a base ref to compute changed-line coverage the same
# way GitHub CI does (merge-base of HEAD against the target branch). This helper
# centralizes that resolution so baas and gateway justfiles stay in sync.
#
# Resolution order:
#   1. AVERNET_LOCAL_TEST_BASE — explicit override; returned verbatim.
#   2. origin/dev — fetched on demand unless AVERNET_LOCAL_TEST_NO_FETCH=1,
#      then `git merge-base HEAD origin/dev`.
#   3. If origin/dev is unavailable and no override is set, fail closed with a
#      message pointing at the escape hatches.
#
# Output: prints the resolved base ref on stdout (single line).
# Returns non-zero on hard failure.

set -euo pipefail

resolve_local_test_base() {
    local target_ref="origin/dev"
    local merge_base

    if [[ -n "${AVERNET_LOCAL_TEST_BASE:-}" ]]; then
        echo "${AVERNET_LOCAL_TEST_BASE}"
        return 0
    fi

    if [[ "${AVERNET_LOCAL_TEST_NO_FETCH:-0}" != "1" ]]; then
        # Tolerate fetch failures (offline, no remote): we still try the local
        # origin/dev ref below. Capture stderr so noise does not leak onto the
        # test output stream.
        git fetch --no-tags origin dev >/dev/null 2>&1 || true
    fi

    if git rev-parse --verify "${target_ref}" >/dev/null 2>&1; then
        merge_base="$(git merge-base HEAD "${target_ref}")" || {
            echo "local test base: could not compute merge-base of HEAD and ${target_ref}" >&2
            echo "set AVERNET_LOCAL_TEST_BASE=<ref>, export AVERNET_LOCAL_TEST_NO_FETCH=1," >&2
            echo "or run 'just test-no-cov' for quick feedback without the coverage gate." >&2
            return 1
        }
        echo "${merge_base}"
        return 0
    fi

    echo "local test base: ${target_ref} is unavailable and AVERNET_LOCAL_TEST_BASE is not set." >&2
    echo "set AVERNET_LOCAL_TEST_BASE=<ref> (e.g. the merge-base yourself)," >&2
    echo "export AVERNET_LOCAL_TEST_NO_FETCH=1 after fetching manually," >&2
    echo "or run 'just test-no-cov' for quick feedback without the coverage gate." >&2
    return 1
}