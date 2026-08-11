#!/usr/bin/env bash
######################################################################
# resolve_base_ref.sh - Resolve the local changed-line coverage base ref
#
# `just test` and `ci_test.sh` enforce the same changed-line coverage gate
# as GitHub CI. CI derives the base ref from `origin/dev` (or `HEAD^1` on
# pull requests); locally we reuse the pre-push merge target contract from
# `.githooks/pre-push` / `scripts/ci/pre_push.sh`, so the manually driven
# `just test` and the hook-driven push describe the same change range.
#
# Resolution order (highest priority first):
#   1. AVERNET_PRE_PUSH_MERGE_TARGET=<remote>/<branch>
#   2. avernet.prePush.mergeTarget git config (<remote>/<branch>)
#   3. origin/dev
#
# The resolved target is fetched shallowly, then `git merge-base HEAD <target>`
# is printed on stdout for the caller to pass as `--base`. When the target
# cannot be fetched or has no merge base with HEAD, the helper fails loudly
# with a remediation hint and never prints a base ref, so the coverage gate
# is never silently skipped (the current bug).
#
# Source this file (or call `resolve_base_ref` after sourcing) rather than
# executing it directly: it only defines the helper and returns the base ref
# via stdout, leaving argument/exit-code handling to the caller.
######################################################################
set -euo pipefail

# Resolve <remote>/<branch> from the merge-target contract, defaulting to
# origin/dev and echoing the chosen target on stdout.
resolve_merge_target() {
  local configured_merge_target
  configured_merge_target="$(git config --get avernet.prePush.mergeTarget 2>/dev/null || true)"
  local merge_target="${AVERNET_PRE_PUSH_MERGE_TARGET:-$configured_merge_target}"
  merge_target="${merge_target:-origin/dev}"
  echo "$merge_target"
}

# Fetch the configured merge target ref and print its commit SHA on stdout.
# Fails loudly with a remediation hint when the target is unreachable or not
# a `<remote>/<branch>` pair, so callers do not fall back to a stale range.
resolve_merge_target_sha() {
  local merge_target target_remote target_branch target_ref

  merge_target="$1"
  if [[ "$merge_target" != */* ]]; then
    echo "error: changed-line coverage base target must use <remote>/<branch>: $merge_target" >&2
    return 1
  fi
  target_remote="${merge_target%%/*}"
  target_branch="${merge_target#*/}"
  if [[ -z "$target_remote" || -z "$target_branch" ]] \
    || ! git remote get-url "$target_remote" >/dev/null 2>&1 \
    || ! git check-ref-format "refs/heads/$target_branch" >/dev/null 2>&1; then
    echo "error: invalid changed-line coverage base target: $merge_target" >&2
    return 1
  fi

  target_ref="refs/remotes/$target_remote/$target_branch"
  if ! git fetch --quiet --no-tags "$target_remote" \
    "+refs/heads/$target_branch:$target_ref"; then
    echo "error: failed to fetch changed-line coverage base target $merge_target" >&2
    echo "hint: run \`git fetch $target_remote $target_branch\` or set AVERNET_PRE_PUSH_MERGE_TARGET=<remote>/<branch>" >&2
    return 1
  fi
  if ! git rev-parse --verify "${target_ref}^{commit}"; then
    echo "error: changed-line coverage base target $merge_target is not a commit" >&2
    return 1
  fi
}

# Resolve and print the changed-line coverage base ref (the merge base of HEAD
# and the configured target) on stdout. Fails loudly so the gate is never
# skipped silently. Optional first argument overrides the merge target.
resolve_base_ref() {
  local merge_target target_sha base

  merge_target="${1:-$(resolve_merge_target)}"
  if ! target_sha="$(resolve_merge_target_sha "$merge_target")"; then
    return 1
  fi
  if ! base="$(git merge-base HEAD "$target_sha")"; then
    echo "error: no merge base between HEAD and $merge_target ($target_sha)" >&2
    return 1
  fi
  echo "$base"
}
