# Avernet root justfile — local development entry points for the
# changed-line coverage gate. The recipes dispatch to
# scripts/ci/local_test.sh, which mirrors scripts/ci/pre_push.sh's module
# selection and GitHub CI's changed-line coverage gate
# (.github/workflows/unit-tests.yml).
#
# Install `just` (https://github.com/casey/just) to use these recipes;
# `bash scripts/ci/local_test.sh ...` remains a fully equivalent fallback.

_default:
    @just --list

# Local tests with the GitHub-CI-consistent changed-line coverage gate.
# Resolves the target branch via (in priority order) --base, AVERNET_TEST_BASE_REF,
# avernet.test.mergeTarget, the upstream tracking branch, or origin/dev
# (fail-closed). Run `just test-base <ref>` to override the baseline explicitly.
test:
    bash scripts/ci/local_test.sh

# Local tests with an explicit baseline (commit-ish or <remote>/<branch>).
test-base ref:
    bash scripts/ci/local_test.sh --base "{{ref}}"

# Fast local iteration: run unit tests WITHOUT the changed-line coverage gate.
# This does NOT satisfy the pre-push / PR coverage gate.
test-no-cov:
    bash scripts/ci/local_test.sh --no-cov