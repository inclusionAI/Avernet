#!/usr/bin/env bash
# Resolve the local `just test` baseline commit for the changed-line coverage
# gate. Mirrors the pre-push merge-target contract (`.githooks/pre-push`) so the
# local gate and GitHub CI use the same diff range for the same target branch.
#
# Resolution priority (high -> low):
#   1. --base <commit-or-ref>     explicit CLI argument
#   2. AVERNET_TEST_BASE_REF       environment variable (same semantics as --base)
#   3. avernet.test.mergeTarget    persistent git config (<remote>/<branch>)
#   4. upstream tracking branch    @{upstream} of the current branch
#   5. origin/dev                  last-resort default, but only if it can be
#                                  fetched and has a merge-base with HEAD;
#                                  otherwise fail closed.
#
# Output: stdout  -> baseline commit SHA (suitable for `ci_test.sh --base`)
#         stderr  -> human-readable diagnostics
# Exit:   0 on success, non-zero with an actionable message otherwise.
# The script never silently falls back to a stale target, an old remote SHA,
# or the root commit.
set -euo pipefail

print_usage() {
  cat >&2 <<'USAGE'
usage: resolve_test_baseline.sh [--base <commit-or-ref>]

Resolve the local-test baseline commit (merge-base with HEAD) used by the
changed-line coverage gate. Specify the target branch explicitly in one of
these ways (highest priority first):

  1. --base <ref>                <remote>/<branch>, branch, or commit-ish
  2. AVERNET_TEST_BASE_REF=<ref> environment variable (same semantics)
  3. git config avernet.test.mergeTarget <remote>/<branch>
  4. (auto) upstream tracking branch of the current branch
  5. (default) origin/dev        only when fetchable and has a merge-base

The script fails closed when no baseline can be resolved; it never silently
falls back to a wrong default or skips the coverage gate.
USAGE
}

fail_closed() {
  echo "error: $1" >&2
  echo "error: could not resolve a local-test baseline." >&2
  echo "error: specify one via (in priority order):" >&2
  echo "error:   --base <commit-or-ref>" >&2
  echo "error:   AVERNET_TEST_BASE_REF=<commit-or-ref>" >&2
  echo "error:   git config avernet.test.mergeTarget <remote>/<branch>" >&2
  echo "error:   set an upstream tracking branch for the current branch" >&2
  echo "error: refusing to fall back to a stale or wrong default." >&2
  exit 1
}

explicit_base=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --base)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --base requires an argument" >&2
        exit 2
      fi
      explicit_base="$2"
      shift 2
      ;;
    -h|--help)
      print_usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      print_usage
      exit 2
      ;;
  esac
done

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" \
  || fail_closed "must run inside a git repository"
cd "$repo_root"

head="HEAD"
if ! head_sha="$(git rev-parse --verify "${head}^{commit}" 2>/dev/null)"; then
  fail_closed "cannot resolve HEAD commit"
fi

# Validate a <remote>/<branch> token, fetch it, and echo its commit SHA on
# stdout. Fails closed on any error.
resolve_remote_branch_sha() {
  local target="$1"
  local target_remote target_branch target_ref target_sha
  if [[ "$target" != */* ]]; then
    fail_closed "merge target must use <remote>/<branch> form: $target"
  fi
  target_remote="${target%%/*}"
  target_branch="${target#*/}"
  if [[ -z "$target_remote" || -z "$target_branch" ]] \
    || ! git remote get-url "$target_remote" >/dev/null 2>&1 \
    || ! git check-ref-format "refs/heads/$target_branch" >/dev/null 2>&1; then
    fail_closed "invalid merge target: $target"
  fi
  target_ref="refs/remotes/$target_remote/$target_branch"
  if ! git fetch --quiet --no-tags "$target_remote" \
        "+refs/heads/$target_branch:$target_ref" 2>/dev/null; then
    fail_closed "failed to fetch merge target $target"
  fi
  if ! target_sha="$(git rev-parse --verify "${target_ref}^{commit}" 2>/dev/null)"; then
    fail_closed "merge target $target is not a commit"
  fi
  printf '%s\n' "$target_sha"
}

# Resolve an arbitrary ref token (CLI arg or env override) to a commit SHA.
# Accepts:
#   - <remote>/<branch>  -> fetch via resolve_remote_branch_sha
#   - branch name        -> resolve via refs/heads/<branch> or refs/remotes/*/<branch>
#   - commit-ish         -> git rev-parse --verify <ref>^{commit}
resolve_ref_to_sha() {
  local token="$1" sha
  if [[ "$token" == */* ]] && git remote get-url "${token%%/*}" >/dev/null 2>&1; then
    # Treat as <remote>/<branch>: fetch then resolve.
    sha="$(resolve_remote_branch_sha "$token")"
  elif [[ "$token" != */* ]] \
    && sha="$(git rev-parse --verify "refs/heads/$token^{commit}" 2>/dev/null)"; then
    : # already resolved
  else
    if ! sha="$(git rev-parse --verify "${token}^{commit}" 2>/dev/null)"; then
      fail_closed "cannot resolve ref to commit: $token"
    fi
  fi
  printf '%s\n' "$sha"
}

# Compute merge-base of HEAD and a resolved target SHA, printing the baseline
# SHA on stdout. Fails closed when there is no common ancestor.
emit_baseline_for_target_sha() {
  local target_sha="$1" base
  if ! base="$(git merge-base "$head_sha" "$target_sha" 2>/dev/null)" || [[ -z "$base" ]]; then
    fail_closed "no merge base between HEAD ($head_sha) and target ($target_sha)"
  fi
  printf '%s\n' "$base"
}

# ---- Priority 1/2: explicit --base / AVERNET_TEST_BASE_REF -----------------
if [[ -n "${AVERNET_TEST_BASE_REF:-}" ]]; then
  if [[ -z "$explicit_base" ]]; then
    explicit_base="$AVERNET_TEST_BASE_REF"
  fi
fi

if [[ -n "$explicit_base" ]]; then
  target_sha="$(resolve_ref_to_sha "$explicit_base")"
  echo "test baseline source: explicit ($explicit_base)" >&2
  echo "test baseline target sha: $target_sha" >&2
  emit_baseline_for_target_sha "$target_sha"
  exit 0
fi

# ---- Priority 3: git config avernet.test.mergeTarget ----------------------
configured_target="$(git config --get avernet.test.mergeTarget 2>/dev/null || true)"
if [[ -n "$configured_target" ]]; then
  target_sha="$(resolve_remote_branch_sha "$configured_target")"
  echo "test baseline source: avernet.test.mergeTarget=$configured_target" >&2
  echo "test baseline target sha: $target_sha" >&2
  emit_baseline_for_target_sha "$target_sha"
  exit 0
fi

# ---- Priority 4: upstream tracking branch ---------------------------------
upstream_ref=""
if upstream_ref="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)" \
  && [[ -n "$upstream_ref" && "$upstream_ref" != "HEAD" ]]; then
  # Fetch the upstream so the resolution reflects the current remote state.
  upstream_remote="${upstream_ref%%/*}"
  upstream_branch="${upstream_ref#*/}"
  if [[ -n "$upstream_remote" && -n "$upstream_branch" ]] \
    && git remote get-url "$upstream_remote" >/dev/null 2>&1; then
    git fetch --quiet --no-tags "$upstream_remote" \
      "+refs/heads/$upstream_branch:refs/remotes/$upstream_remote/$upstream_branch" 2>/dev/null || true
  fi
  if target_sha="$(git rev-parse --verify "${upstream_ref}^{commit}" 2>/dev/null)"; then
    echo "test baseline source: upstream tracking branch ($upstream_ref)" >&2
    echo "test baseline target sha: $target_sha" >&2
    emit_baseline_for_target_sha "$target_sha"
    exit 0
  fi
fi

# ---- Priority 5: default origin/dev, but fail-closed if unusable ---------
if git remote get-url origin >/dev/null 2>&1; then
  if git fetch --quiet --no-tags origin "+refs/heads/dev:refs/remotes/origin/dev" 2>/dev/null \
    && target_sha="$(git rev-parse --verify "refs/remotes/origin/dev^{commit}" 2>/dev/null)"; then
    echo "test baseline source: default origin/dev" >&2
    echo "test baseline target sha: $target_sha" >&2
    emit_baseline_for_target_sha "$target_sha"
    exit 0
  fi
fi

fail_closed "no baseline source resolved (--base, AVERNET_TEST_BASE_REF, avernet.test.mergeTarget, upstream tracking branch, or default origin/dev)"