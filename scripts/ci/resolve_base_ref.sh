#!/usr/bin/env bash
# Resolve the base ref for changed-line coverage comparison.
#
# Resolution precedence (highest to lowest):
#   1. AVERNET_BASE_REF env var — explicit user override
#   2. AVERNET_PRE_PUSH_MERGE_TARGET env var — reuse the pre-push override
#   3. avernet.prePush.mergeTarget git config — persistent worktree override
#   4. Current branch's tracking remote branch
#   5. origin/dev — default when none of the above applies
#
# After resolution, the base ref is verified to be resolvable via git rev-parse.
# If it cannot be resolved, the script fails with actionable guidance.
#
# Output: resolved base ref string on stdout (e.g. "origin/dev").
# Exit: 0 on success, non-zero on failure.
set -euo pipefail

repo_root="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || true)}"
if [[ -z "$repo_root" ]]; then
  echo "error: cannot determine repository root. Run from inside a git repository." >&2
  exit 1
fi
cd "$repo_root"

fallback_ref="${1:-origin/dev}"

resolve_base_ref() {
  local ref=""

  # 1. AVERNET_BASE_REF — explicit user override (highest priority)
  ref="${AVERNET_BASE_REF:-}"
  if [[ -n "$ref" ]]; then
    echo "$ref"
    return 0
  fi

  # 2. AVERNET_PRE_PUSH_MERGE_TARGET — reuse the existing pre-push override
  ref="${AVERNET_PRE_PUSH_MERGE_TARGET:-}"
  if [[ -n "$ref" ]]; then
    echo "$ref"
    return 0
  fi

  # 3. avernet.prePush.mergeTarget git config — persistent worktree override
  ref="$(git config --worktree avernet.prePush.mergeTarget 2>/dev/null || true)"
  if [[ -n "$ref" ]]; then
    echo "$ref"
    return 0
  fi

  # 4. Current branch's tracking remote branch
  local upstream
  upstream="$(git rev-parse --abbrev-ref --symbolic-full-name HEAD@{upstream} 2>/dev/null || true)"
  if [[ -n "$upstream" ]]; then
    echo "$upstream"
    return 0
  fi

  # 5. Fallback (default: origin/dev)
  echo "$fallback_ref"
  return 0
}

base_ref="$(resolve_base_ref)"

# Verify the base ref is resolvable
if ! git rev-parse --verify "$base_ref" >/dev/null 2>&1; then
  echo "error: cannot resolve base ref '${base_ref}'." >&2
  echo "Specify a valid base explicitly:" >&2
  echo "  AVERNET_BASE_REF=origin/dev just test" >&2
  echo "  git config --worktree avernet.prePush.mergeTarget origin/dev" >&2
  exit 1
fi

echo "$base_ref"