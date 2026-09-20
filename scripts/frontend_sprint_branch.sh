#!/usr/bin/env bash
# frontend_sprint_branch.sh — record the sprint branch to track for the
# external teamclaw checkout (TEAMCLAW_DIR) AFTER validating that the branch
# exists on the checkout's remote. Avernet has no submodule to carry this
# state in-tree, so the record is a config file:
#   authority: FRONTEND_SPRINT_BRANCH env > the state file below
#   state file: ${FRONTEND_SPRINT_CONFIG:-${HOME}/.config/teamclaw-frontend/branch}
# Humans and coding agents share this CLI.
#
# 用法: frontend_sprint_branch.sh <branch> | --list
#   <branch>  a sprint_teamclaw_* branch on the remote (--list shows what exists)
#   --list    print the recorded branch and the remote's sprint heads
#
# Validate-then-write: an unknown branch (or an unreachable remote) leaves
# the state file byte-identical. The one write is the config file — the tool
# never touches the checkout itself; advancing it is the operator's
# follow-up (fetch + checkout, then singlebox.sh frontend-pull), not a side
# effect here.
#
# exit codes: 0 ok · 1 operational failure · 2 usage error.
set -euo pipefail

# No baked-in remote URL: this is an open-source repo, and the teamclaw remote
# is internal — cloning it is only possible from inside the company anyway.
# The checkout's own origin (what frontend-pull fetches) is the only authority
# this tool accepts; no resolvable checkout → hard refusal.
SPRINT_CONFIG="${FRONTEND_SPRINT_CONFIG:-${HOME}/.config/teamclaw-frontend/branch}"
BRANCH_HEADS_PATTERN='refs/heads/sprint_teamclaw*'

usage() {
  cat <<'EOF'
usage: frontend_sprint_branch.sh <branch> | --list

Record the sprint branch to track for the external teamclaw checkout
(TEAMCLAW_DIR), after ls-remote validation of the checkout's own remote.
The record is a per-operator state file (FRONTEND_SPRINT_BRANCH env
overrides it until unset); the checkout itself is never touched — the
follow-up (git fetch && git checkout <branch>, then
./scripts/singlebox.sh frontend-pull) is yours to run.
--list prints the recorded branch and the remote's sprint_teamclaw_* heads.

exit codes: 0 ok · 1 operational failure · 2 usage error.
EOF
}

# Same transport envelope as bounded_git in scripts/modules/frontend.sh
# (inlined here — this CLI does not source the module; keep the two in sync:
# git has no default low-speed abort, so a stalling remote must fail in ~10s
# instead of parking the CLI in a connect-timeout limbo).
bounded_git() {
  GIT_SSH_COMMAND="ssh -o ConnectTimeout=10" \
    git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=10 "$@"
}

# Remote authority: the teamclaw checkout's own origin (the same spelling
# frontend-pull reads). No fallback URL — the teamclaw remote is internal
# and must not be spelled out in this open-source repository; without a
# resolvable checkout the tool refuses rather than guessing where to look.
resolve_repo_url() {
  REPO_URL=""
  if [ -n "${TEAMCLAW_DIR:-}" ] && \
     git -C "${TEAMCLAW_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    REPO_URL="$(git -C "${TEAMCLAW_DIR}" config --get remote.origin.url 2>/dev/null || true)"
  fi
}

# The declared branch: FRONTEND_SPRINT_BRANCH env > the state file (first
# line). Prints nothing (rc 0) when neither is set.
recorded_branch() {
  if [ -n "${FRONTEND_SPRINT_BRANCH:-}" ]; then
    printf '%s\n' "${FRONTEND_SPRINT_BRANCH}"
    return 0
  fi
  [ -f "${SPRINT_CONFIG}" ] && head -n 1 "${SPRINT_CONFIG}"
  return 0
}

# Help exits before anything else: no git read, no remote roundtrip, rc 0.
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac

resolve_repo_url
if [ -z "${REPO_URL}" ]; then
    printf 'error: no teamclaw checkout to resolve the remote from.\n' >&2
    printf 'set TEAMCLAW_DIR to your teamclaw checkout (its origin is the' >&2
    printf ' remote this tool validates against); no fallback URL is baked in.\n' >&2
    exit 1
fi

if [ "${1:-}" = "--list" ]; then
    if [ "$#" -gt 1 ]; then
        printf 'error: --list takes no additional argument(s): %s\n' "${*:2}" >&2
        usage >&2
        exit 2
    fi
    declared="$(recorded_branch || true)"
    printf 'declared: %s\n' "${declared:-<none>}"
    printf 'config: %s\n' "${SPRINT_CONFIG}"
    printf 'remote: %s\n' "${REPO_URL}"
    printf 'remote sprint heads:\n'
    if ! ls_out="$(bounded_git ls-remote --heads "${REPO_URL}" \
        "${BRANCH_HEADS_PATTERN}" 2>&1)"; then
        printf 'error: cannot reach %s\n' "${REPO_URL}" >&2
        printf '%s\n' "${ls_out}" >&2
        exit 1
    fi
    # An empty answer from a reachable remote is a valid (worrying) state,
    # not an error: the next rollover has nothing to switch to yet.
    printf '%s\n' "${ls_out}" | sed 's|refs/heads/|  |'
    exit 0
fi

if [ "$#" -eq 0 ]; then
    usage >&2
    exit 2
fi
if [ "$#" -gt 1 ]; then
    printf 'error: unexpected argument(s): %s\n' "${*:2}" >&2
    usage >&2
    exit 2
fi
BR="$1"

# Validate before any write: the branch must be a head on the remote. A wrong
# name landing in the state file would point every later rollover checkout at
# a ref that does not exist. The ls-remote pattern is anchored under
# refs/heads/, and the match must be exact: a name carrying glob metachars
# (e.g. "sprint_teamclaw_*") would otherwise validate against heads it merely
# matches.
if ! ls_out="$(bounded_git ls-remote --heads "${REPO_URL}" "refs/heads/${BR}" 2>&1)"; then
    printf 'error: cannot reach %s\n' "${REPO_URL}" >&2
    printf '%s\n' "${ls_out}" >&2
    exit 1
fi
if ! printf '%s\n' "${ls_out}" | \
    awk -v want="refs/heads/${BR}" '$2 == want { found = 1 } END { exit found ? 0 : 1 }'; then
    printf 'error: branch not found on remote: %s (run with --list to see what exists)\n' "${BR}" >&2
    exit 1
fi

# The only write: the state file (one line, the branch name).
if ! { mkdir -p "$(dirname "${SPRINT_CONFIG}")" && printf '%s\n' "${BR}" >"${SPRINT_CONFIG}"; } then
    printf 'error: failed to write %s\n' "${SPRINT_CONFIG}" >&2
    exit 1
fi

printf 'recorded sprint branch %s in %s; when ready:\n' "${BR}" "${SPRINT_CONFIG}"
if [ -n "${FRONTEND_SPRINT_BRANCH:-}" ] && [ "${FRONTEND_SPRINT_BRANCH}" != "${BR}" ]; then
    printf 'note: FRONTEND_SPRINT_BRANCH=%s is set in the environment and keeps overriding the file until unset\n' \
      "${FRONTEND_SPRINT_BRANCH}"
fi
if [ -n "${TEAMCLAW_DIR:-}" ]; then
    printf '  git -C "%s" fetch && git -C "%s" checkout %s\n' "${TEAMCLAW_DIR}" "${TEAMCLAW_DIR}" "${BR}"
else
    printf '  git -C <TEAMCLAW_DIR> fetch && git -C <TEAMCLAW_DIR> checkout %s\n' "${BR}"
fi
printf '  ./scripts/singlebox.sh frontend-pull\n'
