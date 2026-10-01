#!/usr/bin/env bash
# Shared defaults for BCS architecture CI scripts.
#
# Company CI should prefer passing BCS_BASE_REF at runtime, for example:
#   BCS_BASE_REF=origin/dev bash scripts/ci/arch-check.sh
#
# Until the CI platform injects the target branch, change this single default
# if the repository's integration branch is not origin/master.
BCS_DEFAULT_BASE_REF="${BCS_DEFAULT_BASE_REF:-origin/refactor_arch_bcs}"

resolve_bcs_base_ref() {
  local repo_dir=${1:-.}

  if [[ -n "${BCS_BASE_REF:-}" ]]; then
    echo "$BCS_BASE_REF"
    return
  fi

  if [[ -n "${GITHUB_BASE_REF:-}" ]]; then
    echo "$GITHUB_BASE_REF"
    return
  fi

  if [[ -n "${BCS_DEFAULT_BASE_REF:-}" ]]; then
    echo "$BCS_DEFAULT_BASE_REF"
    return
  fi

  if default_ref=$(git -C "$repo_dir" symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null); then
    echo "${default_ref#refs/remotes/}"
    return
  fi

  echo "origin/HEAD"
}
